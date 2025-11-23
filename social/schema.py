import graphene
from graphene import relay
from graphene_django import DjangoObjectType
from graphene_django.filter import DjangoFilterConnectionField
import graphql_jwt
from graphql_jwt.decorators import login_required
from promise import Promise
from promise.dataloader import DataLoader
from django.db.models import F, Q
from django.utils import timezone

from .models import (
    Post,
    Comment,
    Reaction,
    Profile,
    Follow,
    Hashtag,
    PostHashtag,
    Notification,
    User,
)

# -----------------------------
# DataLoaders (attach per-request via info.context.loaders)
# -----------------------------
class ProfileLoader(DataLoader):
    def batch_load_fn(self, user_ids):
        profiles = Profile.objects.filter(user_id__in=user_ids)
        mapping = {uid: None for uid in user_ids}
        for p in profiles:
            mapping[p.user_id] = p
        return Promise.resolve([mapping[uid] for uid in user_ids])

class PostsByUserLoader(DataLoader):
    def batch_load_fn(self, user_ids):
        posts = (
            Post.objects.filter(author_id__in=user_ids, is_deleted=False)
            .order_by('-created_at')
        )
        mapping = {uid: [] for uid in user_ids}
        for p in posts:
            mapping[p.author_id].append(p)
        return Promise.resolve([mapping[uid] for uid in user_ids])

# Helper to get or create per-request loaders
def get_loaders(context):
    if not hasattr(context, 'loaders'):
        context.loaders = {}
    if 'profile_loader' not in context.loaders:
        context.loaders['profile_loader'] = ProfileLoader()
    if 'posts_by_user_loader' not in context.loaders:
        context.loaders['posts_by_user_loader'] = PostsByUserLoader()
    return context.loaders

# -----------------------------
# Graphene Types
# -----------------------------
class PostNode(DjangoObjectType):
    class Meta:
        model = Post
        interfaces = (relay.Node,)
        filter_fields = {
            'author__id': ['exact'],
            'visibility': ['exact'],
            'created_at': ['lte', 'gte'],
        }

    def resolve_author(self, info):
        loaders = get_loaders(info.context)
        return loaders['profile_loader'].load(self.author_id)
        

class CommentNode(DjangoObjectType):
    class Meta:
        model = Comment
        interfaces = (relay.Node,)
        filter_fields = {
            'author__id': ['exact'],
            'post__id': ['exact'],
            'created_at': ['lte', 'gte'],
        }

class ReactionNode(DjangoObjectType):
    class Meta:
        model = Reaction

class ProfileNode(DjangoObjectType):
    class Meta:
        model = Profile

class FollowNode(DjangoObjectType):
    class Meta:
        model = Follow

class HashtagNode(DjangoObjectType):
    class Meta:
        model = Hashtag

class NotificationNode(DjangoObjectType):
    class Meta:
        model = Notification

# -----------------------------
# Queries (feed + trending + basic)
# -----------------------------
class Query(graphene.ObjectType):
    node = relay.Node.Field()

    # Relay-style connection fields (support cursor pagination)
    all_posts = DjangoFilterConnectionField(PostNode)
    post = graphene.Field(PostNode, id=graphene.Int())

    # Feed queries
    following_feed = DjangoFilterConnectionField(PostNode, description="Posts from users you follow")
    trending_feed = DjangoFilterConnectionField(PostNode, description="Trending posts (recent + engagement)")

    @login_required
    def resolve_all_posts(self, info, **kwargs):
        return Post.objects.filter(is_deleted=False).order_by('-created_at')

    @login_required
    def resolve_post(self, info, id):
        try:
            return Post.objects.get(pk=id, is_deleted=False)
        except Post.DoesNotExist:
            return None

    @login_required
    def resolve_following_feed(self, info, **kwargs):
        user = info.context.user
        # get ids the user follows (exclude self)
        following_ids = list(Follow.objects.filter(follower=user).values_list('following_id', flat=True))
        if not following_ids:
            return Post.objects.none()
        return Post.objects.filter(author_id__in=following_ids, is_deleted=False).order_by('-created_at')

    @login_required
    def resolve_trending_feed(self, info, **kwargs):
        # Simple trending: weighted likes/comments within the last 7 days
        since = timezone.now() - timezone.timedelta(days=7)
        posts = (
            Post.objects.filter(is_deleted=False, created_at__gte=since)
            .annotate(score=(F('like_count') * 2 + F('comment_count') * 3))
            .order_by('-score', '-created_at')
        )
        return posts

# -----------------------------
# Mutations
# -----------------------------


class CreateUser(graphene.Mutation):
    user = graphene.Field(ProfileNode)

    class Arguments:
        username = graphene.String(required=True)
        password = graphene.String(required=True)

    def mutate(self, info, username, password):
        from django.contrib.auth.models import User

        user = User.objects.create_user(username=username, password=password)
        profile = Profile.objects.create(user=user)
        return CreateUser(user=profile)


class CreatePost(graphene.Mutation):
    post = graphene.Field(PostNode)

    class Arguments:
        content = graphene.String(required=True)
        visibility = graphene.String(required=False)

    @login_required
    def mutate(self, info, content, visibility='public'):
        user = info.context.user
        post = Post.objects.create(author=user, content=content, visibility=visibility)

        # Fan-out: create timeline entries for followers (LIMITED to protect DB - in prod use background worker)
        from django.db import transaction
        follower_ids = list(Follow.objects.filter(following=user).values_list('follower_id', flat=True))
        MAX_FANOUT = 1000
        if follower_ids:
            to_insert = []
            now = timezone.now()
            for fid in follower_ids[:MAX_FANOUT]:
                to_insert.append(Timeline(user_id=fid, post=post, author=user, created_at=now))
            if to_insert:
                Timeline.objects.bulk_create(to_insert)

        return CreatePost(post=post)

class CreateComment(graphene.Mutation):
    comment = graphene.Field(CommentNode)

    class Arguments:
        post_id = graphene.Int(required=True)
        content = graphene.String(required=True)

    @login_required
    def mutate(self, info, post_id, content):
        user = info.context.user
        post = Post.objects.get(pk=post_id)
        comment = Comment.objects.create(author=user, post=post, content=content)
        # update denormalized count (atomic increment would be preferable)
        Post.objects.filter(pk=post.pk).update(comment_count=F('comment_count') + 1)
        # create notification
        Notification.objects.create(recipient=post.author, actor=user, type=Notification.COMMENT, post=post)
        return CreateComment(comment=comment)

class ReactToPost(graphene.Mutation):
    ok = graphene.Boolean()

    class Arguments:
        post_id = graphene.Int(required=True)
        reaction_type = graphene.String(required=True)

    @login_required
    def mutate(self, info, post_id, reaction_type):
        user = info.context.user
        post = Post.objects.get(pk=post_id)
        reaction, created = Reaction.objects.get_or_create(post=post, user=user, type=reaction_type)
        if created:
            Post.objects.filter(pk=post.pk).update(like_count=F('like_count') + 1)
            Notification.objects.create(recipient=post.author, actor=user, type=Notification.LIKE, post=post)
        return ReactToPost(ok=True)

class ObtainJSONWebToken(graphql_jwt.ObtainJSONWebToken):
    user = graphene.Field(ProfileNode)

    @classmethod
    def resolve(cls, root, info, **kwargs):
        return cls

class Mutation(graphene.ObjectType):
    token_auth = graphql_jwt.ObtainJSONWebToken.Field()
    refresh_token = graphql_jwt.Refresh.Field()
    verify_token = graphql_jwt.Verify.Field()

    create_user = CreateUser.Field()
    create_post = CreatePost.Field()
    create_comment = CreateComment.Field()
    react_to_post = ReactToPost.Field()

# -----------------------------
# Schema
# -----------------------------
schema = graphene.Schema(query=Query, mutation=Mutation)