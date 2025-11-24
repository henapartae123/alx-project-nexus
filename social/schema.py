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
    Timeline
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
    author = graphene.Field(lambda: ProfileNode)
    comments = graphene.List(lambda: CommentNode)

    class Meta:
        model = Post
        interfaces = (relay.Node,)
        fields = "__all__"
        filter_fields = {
            'author__id': ['exact'],
            'visibility': ['exact'],
            'created_at': ['lte', 'gte'],
        }

    def resolve_author(self, info):
        return self.author
        # loaders = get_loaders(info.context)
        # return loaders['profile_loader'].load(self.author_id)

    def resolve_comments(self, info):
        return self.comments.all().order_by('-created_at')


class CommentNode(DjangoObjectType):
    author = graphene.Field(lambda: ProfileNode)

    class Meta:
        model = Comment
        interfaces = (relay.Node,)
        filter_fields = {
            'author__id': ['exact'],
            'post__id': ['exact'],
        }

    def resolve_author(self, info):
        loaders = get_loaders(info.context)
        return loaders['profile_loader'].load(self.author_id)

class ProfileNode(DjangoObjectType):
    posts = DjangoFilterConnectionField(lambda: PostNode)
    followers = DjangoFilterConnectionField(lambda: ProfileNode)
    following = DjangoFilterConnectionField(lambda: ProfileNode)

    class Meta:
        model = Profile
        interfaces = (relay.Node,)
        fields = "__all__"
        filter_fields = {
            "display_name": ["icontains"],
            "bio": ["icontains"],
        }

    def resolve_posts(self, info, **kwargs):
        return Post.objects.filter(author=self).order_by("-created_at")
    
    # Resolve followers: users who follow this profile
    def resolve_followers(self, info, **kwargs):
        follower_ids = Follow.objects.filter(following=self.user).values_list('follower_id', flat=True)
        return Profile.objects.filter(user_id__in=follower_ids)

    # Resolve following: users whom this profile is following
    def resolve_following(self, info, **kwargs):
        following_ids = Follow.objects.filter(follower=self.user).values_list('following_id', flat=True)
        return Profile.objects.filter(user_id__in=following_ids)

class FollowNode(DjangoObjectType):
    class Meta:
        model = Follow
        interfaces = (relay.Node,)
        fields = "__all__"
        filter_fields = {
            "follower__username": ["exact"],
            "following__username": ["exact"],
        }

class TimelineNode(DjangoObjectType):
    class Meta:
        model = Timeline
        interfaces = (relay.Node,)
        filter_fields = {
            "user__username": ["exact"],
        }

class ReactionNode(DjangoObjectType):
    class Meta:
        model = Reaction

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
    comments_for_post = graphene.List(CommentNode, post_id=graphene.Int(required=True))
    profile_by_user = graphene.Field(ProfileNode, user_id=graphene.Int(required=True))

    # Feed queries
    following_feed = DjangoFilterConnectionField(PostNode, description="Posts from users you follow")
    trending_feed = DjangoFilterConnectionField(PostNode, description="Trending posts (recent + engagement)")

    @login_required
    def resolve_all_posts(self, info, **kwargs):
        return Post.objects.filter(is_deleted=False).order_by('-created_at')

    @login_required
    def resolve_comments_for_post(self, info, post_id):
        return Comment.objects.filter(post_id=post_id).order_by('-created_at')
    
    @login_required
    def resolve_post(self, info, id):
        try:
            return Post.objects.get(pk=id, is_deleted=False)
        except Post.DoesNotExist:
            return None
    
    @login_required
    def resolve_profile_by_user(self, info, user_id):
        return Profile.objects.get(user_id=user_id)

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


class FollowUser(relay.ClientIDMutation):
    class Input:
        user_id = graphene.ID(required=True)

    ok = graphene.Boolean()
    follow = graphene.Field(FollowNode)

    @login_required
    def mutate_and_get_payload(root, info, user_id):
        follower = info.context.user
        following = relay.Node.get_node_from_global_id(info, user_id, UserNode)

        follow_obj, _ = Follow.objects.get_or_create(follower=follower, following=following)
        return FollowUser(ok=True, follow=follow_obj)


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

    follow_user = FollowUser.Field()

# -----------------------------
# Schema
# -----------------------------
schema = graphene.Schema(query=Query, mutation=Mutation)