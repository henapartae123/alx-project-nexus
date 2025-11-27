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
from django.contrib.auth import get_user_model

from .models import (
    Post,
    Comment,
    Reaction,
    Profile,
    Follow,
    Hashtag,
    PostHashtag,
    Notification,
    Timeline
)

from graphql_relay import from_global_id

User = get_user_model()

def get_node_id_from_global_id(global_id):
    """
    Extract numeric ID from Relay global ID
    Example: "UG9zdE5vZGU6Mg==" -> 2
    """
    try:
        node_type, node_id = from_global_id(global_id)
        return int(node_id)
    except (ValueError, TypeError, Exception):
        # If it's already a numeric ID, return it
        try:
            return int(global_id)
        except (ValueError, TypeError):
            return None

# -----------------------------
# DataLoaders (attach per-request via info.context.loaders)
# -----------------------------
class ProfileLoader(DataLoader):
    def batch_load_fn(self, user_ids):
        profiles = Profile.objects.filter(user_id__in=user_ids).select_related('user')
        mapping = {p.user_id: p for p in profiles}
        # Return None for missing profiles instead of raising error
        return Promise.resolve([mapping.get(uid) for uid in user_ids])

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

class UserNode(DjangoObjectType):
    """User type for authentication and basic user info"""
    class Meta:
        model = User
        interfaces = (relay.Node,)
        fields = ("id", "username", "email", "is_active", "date_joined")
        filter_fields = {
            'username': ['exact', 'icontains'],
        }


class ProfileNode(DjangoObjectType):
    posts = graphene.List(lambda: PostNode)
    followers = graphene.List(lambda: ProfileNode)
    following = graphene.List(lambda: ProfileNode)
    follower_count = graphene.Int()
    following_count = graphene.Int()

    class Meta:
        model = Profile
        interfaces = (relay.Node,)
        fields = "__all__"
        filter_fields = {
            "display_name": ["icontains"],
            "bio": ["icontains"],
        }

    def resolve_posts(self, info, **kwargs):
        return Post.objects.filter(author=self.user, is_deleted=False).order_by("-created_at")
    
    def resolve_follower_count(self, info):
        return Follow.objects.filter(following=self.user).count()
    
    def resolve_following_count(self, info):
        return Follow.objects.filter(follower=self.user).count()
    
    # Resolve followers: users who follow this profile
    def resolve_followers(self, info, **kwargs):
        follower_ids = Follow.objects.filter(following=self.user).values_list('follower_id', flat=True)
        return Profile.objects.filter(user_id__in=follower_ids)

    # Resolve following: users whom this profile is following
    def resolve_following(self, info, **kwargs):
        following_ids = Follow.objects.filter(follower=self.user).values_list('following_id', flat=True)
        return Profile.objects.filter(user_id__in=following_ids)


class PostNode(DjangoObjectType):
    author = graphene.Field(ProfileNode)
    comments = graphene.List(lambda: CommentNode)
    reactions = graphene.List(lambda: ReactionNode)
    hashtags = graphene.List(lambda: HashtagNode)

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
        # Direct database query instead of DataLoader for now
        # This avoids serialization issues
        try:
            return Profile.objects.select_related('user').get(user_id=self.author_id)
        except Profile.DoesNotExist:
            return None

    def resolve_comments(self, info):
        return self.comments.all().order_by('-created_at')
    
    def resolve_reactions(self, info):
        return self.reactions.all()
    
    def resolve_hashtags(self, info):
        hashtag_ids = PostHashtag.objects.filter(post=self).values_list('hashtag_id', flat=True)
        return Hashtag.objects.filter(id__in=hashtag_ids)


class CommentNode(DjangoObjectType):
    author = graphene.Field(ProfileNode)

    class Meta:
        model = Comment
        interfaces = (relay.Node,)
        fields = "__all__"
        filter_fields = {
            'author__id': ['exact'],
            'post__id': ['exact'],
        }

    def resolve_author(self, info):
        try:
            return Profile.objects.select_related('user').get(user_id=self.author_id)
        except Profile.DoesNotExist:
            return None


class ReactionNode(DjangoObjectType):
    user = graphene.Field(ProfileNode)
    
    class Meta:
        model = Reaction
        interfaces = (relay.Node,)
        fields = "__all__"
    
    def resolve_user(self, info):
        try:
            return Profile.objects.select_related('user').get(user_id=self.user_id)
        except Profile.DoesNotExist:
            return None


class HashtagNode(DjangoObjectType):
    post_count = graphene.Int()
    
    class Meta:
        model = Hashtag
        interfaces = (relay.Node,)
        fields = "__all__"
    
    def resolve_post_count(self, info):
        return PostHashtag.objects.filter(hashtag=self).count()


class NotificationNode(DjangoObjectType):
    recipient = graphene.Field(ProfileNode)
    actor = graphene.Field(ProfileNode)
    
    class Meta:
        model = Notification
        interfaces = (relay.Node,)
        fields = "__all__"
        filter_fields = {
            'recipient__id': ['exact'],
            'is_read': ['exact'],
        }
    
    def resolve_recipient(self, info):
        try:
            return Profile.objects.select_related('user').get(user_id=self.recipient_id)
        except Profile.DoesNotExist:
            return None
    
    def resolve_actor(self, info):
        try:
            return Profile.objects.select_related('user').get(user_id=self.actor_id)
        except Profile.DoesNotExist:
            return None


class FollowNode(DjangoObjectType):
    follower = graphene.Field(ProfileNode)
    following = graphene.Field(ProfileNode)
    
    class Meta:
        model = Follow
        interfaces = (relay.Node,)
        fields = "__all__"
        filter_fields = {
            "follower__username": ["exact"],
            "following__username": ["exact"],
        }
    
    def resolve_follower(self, info):
        try:
            return Profile.objects.select_related('user').get(user_id=self.follower_id)
        except Profile.DoesNotExist:
            return None
    
    def resolve_following(self, info):
        try:
            return Profile.objects.select_related('user').get(user_id=self.following_id)
        except Profile.DoesNotExist:
            return None


class TimelineNode(DjangoObjectType):
    post = graphene.Field(PostNode)
    author = graphene.Field(ProfileNode)
    
    class Meta:
        model = Timeline
        interfaces = (relay.Node,)
        fields = "__all__"
        filter_fields = {
            "user__username": ["exact"],
        }
    
    def resolve_author(self, info):
        try:
            return Profile.objects.select_related('user').get(user_id=self.author_id)
        except Profile.DoesNotExist:
            return None


# -----------------------------
# Queries (feed + trending + basic)
# -----------------------------
class Query(graphene.ObjectType):
    node = relay.Node.Field()

    # User & Profile
    me = graphene.Field(ProfileNode, description="Get current user's profile")
    user = graphene.Field(UserNode, id=graphene.Int())
    profile = graphene.Field(ProfileNode, user_id=graphene.Int())
    profile_by_username = graphene.Field(ProfileNode, username=graphene.String(required=True))
    
    # Posts
    all_posts = graphene.List(PostNode)
    post = graphene.Field(PostNode, id=graphene.Int())
    
    # Comments
    comments_for_post = graphene.List(CommentNode, post_id=graphene.Int(required=True))
    
    # Notifications
    my_notifications = graphene.List(NotificationNode, unread_only=graphene.Boolean())
    
    # Feed queries
    following_feed = graphene.List(PostNode, description="Posts from users you follow")
    trending_feed = graphene.List(PostNode, description="Trending posts (recent + engagement)")
    timeline_feed = graphene.List(TimelineNode, description="Optimized timeline feed")

    @login_required
    def resolve_me(self, info):
        user = info.context.user
        try:
            return Profile.objects.select_related('user').get(user=user)
        except Profile.DoesNotExist:
            return None
    
    def resolve_user(self, info, id):
        try:
            return User.objects.get(pk=id)
        except User.DoesNotExist:
            return None
    
    def resolve_profile(self, info, user_id):
        try:
            return Profile.objects.select_related('user').get(user_id=user_id)
        except Profile.DoesNotExist:
            return None
    
    def resolve_profile_by_username(self, info, username):
        try:
            user = User.objects.get(username=username)
            return Profile.objects.select_related('user').get(user=user)
        except (User.DoesNotExist, Profile.DoesNotExist):
            return None

    # @login_required
    def resolve_all_posts(self, info, **kwargs):
        return Post.objects.filter(is_deleted=False).select_related('author').order_by('-created_at')

    @login_required
    def resolve_comments_for_post(self, info, post_id):
        return Comment.objects.filter(post_id=post_id).select_related('author').order_by('-created_at')
    
    @login_required
    def resolve_post(self, info, id):
        try:
            return Post.objects.select_related('author').get(pk=id, is_deleted=False)
        except Post.DoesNotExist:
            return None
    
    @login_required
    def resolve_my_notifications(self, info, unread_only=False):
        user = info.context.user
        qs = Notification.objects.filter(recipient=user).select_related('actor', 'post').order_by('-created_at')
        if unread_only:
            qs = qs.filter(is_read=False)
        return qs

    @login_required
    def resolve_following_feed(self, info, **kwargs):
        user = info.context.user
        # get ids the user follows
        following_ids = list(Follow.objects.filter(follower=user).values_list('following_id', flat=True))
        if not following_ids:
            return []
        return Post.objects.filter(
            author_id__in=following_ids, 
            is_deleted=False
        ).select_related('author').order_by('-created_at')

    @login_required
    def resolve_trending_feed(self, info, **kwargs):
        # Simple trending: weighted likes/comments within the last 7 days
        since = timezone.now() - timezone.timedelta(days=7)
        posts = (
            Post.objects.filter(is_deleted=False, created_at__gte=since)
            .select_related('author')
            .annotate(score=(F('like_count') * 2 + F('comment_count') * 3))
            .order_by('-score', '-created_at')
        )
        return posts
    
    @login_required
    def resolve_timeline_feed(self, info, **kwargs):
        user = info.context.user
        return Timeline.objects.filter(user=user).select_related('post', 'post__author', 'author')


# -----------------------------
# Mutations
# -----------------------------

class TokenAuth(graphene.Mutation):
    """Custom token authentication with refresh token"""
    token = graphene.String()
    refresh_token = graphene.String()
    user = graphene.Field(ProfileNode)

    class Arguments:
        username = graphene.String(required=True)
        password = graphene.String(required=True)

    def mutate(self, info, username, password):
        from django.contrib.auth import authenticate
        from graphql_jwt.utils import jwt_encode, jwt_payload
        from graphql_jwt.refresh_token.shortcuts import create_refresh_token
        
        # Authenticate
        user = authenticate(username=username, password=password)
        
        if user is None:
            raise Exception('Please enter valid credentials')
        
        if not user.is_active:
            raise Exception('User account is disabled')
        
        # Generate tokens
        payload = jwt_payload(user)
        token = jwt_encode(payload)
        refresh_token_obj = create_refresh_token(user)
        
        # Get profile
        try:
            profile = Profile.objects.select_related('user').get(user=user)
        except Profile.DoesNotExist:
            profile = None
        
        return TokenAuth(
            token=token,
            refresh_token=refresh_token_obj.token,
            user=profile
        )

class CreateUser(graphene.Mutation):
    user = graphene.Field(ProfileNode)
    token = graphene.String()
    refresh_token = graphene.String()

    class Arguments:
        username = graphene.String(required=True)
        password = graphene.String(required=True)
        email = graphene.String()
        display_name = graphene.String()

    def mutate(self, info, username, password, email=None, display_name=None):
        from django.contrib.auth import authenticate
        from graphql_jwt.shortcuts import get_token
        from graphql_jwt.refresh_token.shortcuts import create_refresh_token
        
        try:
            # Create user
            user = User.objects.create_user(
                username=username, 
                password=password,
                email=email or ''
            )
            
            # Create profile
            profile = Profile.objects.create(
                user=user,
                display_name=display_name or username
            )
            
            # Generate JWT token
            token = get_token(user)
            
            # Create refresh token (proper way)
            refresh_token = create_refresh_token(user)
            
            return CreateUser(
                user=profile,
                token=token,
                refresh_token=refresh_token.token  # Return the token string
            )
        except Exception as e:
            raise Exception(f"Error creating user: {str(e)}")


class CreatePost(graphene.Mutation):
    post = graphene.Field(PostNode)

    class Arguments:
        content = graphene.String(required=True)
        visibility = graphene.String(required=False)

    @login_required
    def mutate(self, info, content, visibility='public'):
        user = info.context.user
        post = Post.objects.create(author=user, content=content, visibility=visibility)

        # Fan-out: create timeline entries for followers (LIMITED to protect DB)
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
        post_id = graphene.String(required=True)
        content = graphene.String(required=True)

    @login_required
    def mutate(self, info, post_id, content):
        user = info.context.user
        try:
            post = Post.objects.get(pk=post_id, is_deleted=False)
        except Post.DoesNotExist:
            raise Exception("Post not found")
        
        comment = Comment.objects.create(author=user, post=post, content=content)
        
        # Update denormalized count
        Post.objects.filter(pk=post.pk).update(comment_count=F('comment_count') + 1)
        
        # Create notification (don't notify yourself)
        if post.author != user:
            Notification.objects.create(
                recipient=post.author, 
                actor=user, 
                type=Notification.COMMENT, 
                post=post
            )
        
        return CreateComment(comment=comment)


class ReactToPost(graphene.Mutation):
    ok = graphene.Boolean()
    reaction = graphene.Field(ReactionNode)

    class Arguments:
        post_id = graphene.String(required=True)  # Changed from Int to String
        reaction_type = graphene.String(required=False)

    @login_required
    def mutate(self, info, post_id, reaction_type='like'):
        user = info.context.user
        
        # Convert Relay global ID to numeric ID
        numeric_post_id = get_node_id_from_global_id(post_id)
        
        if not numeric_post_id:
            raise Exception(f"Invalid post ID: {post_id}")
        
        try:
            post = Post.objects.get(pk=numeric_post_id, is_deleted=False)
        except Post.DoesNotExist:
            raise Exception("Post not found")
        
        reaction, created = Reaction.objects.get_or_create(
            post=post, 
            user=user, 
            defaults={'type': reaction_type}
        )
        
        if created:
            Post.objects.filter(pk=post.pk).update(like_count=F('like_count') + 1)
            
            # Create notification (don't notify yourself)
            if post.author != user:
                Notification.objects.create(
                    recipient=post.author, 
                    actor=user, 
                    type=Notification.LIKE, 
                    post=post
                )
        
        return ReactToPost(ok=True, reaction=reaction)


class FollowUser(graphene.Mutation):
    ok = graphene.Boolean()
    follow = graphene.Field(FollowNode)

    class Arguments:
        user_id = graphene.String(required=True)  # Changed from Int to String

    @login_required
    def mutate(self, info, user_id):
        follower = info.context.user
        
        # Convert Relay global ID to numeric ID
        numeric_user_id = get_node_id_from_global_id(user_id)
        
        if not numeric_user_id:
            raise Exception(f"Invalid user ID: {user_id}")
        
        try:
            following = User.objects.get(pk=numeric_user_id)
        except User.DoesNotExist:
            raise Exception("User not found")
        
        if follower == following:
            raise Exception("Cannot follow yourself")

        follow_obj, created = Follow.objects.get_or_create(
            follower=follower, 
            following=following
        )
        
        if created:
            # Create notification
            Notification.objects.create(
                recipient=following,
                actor=follower,
                type=Notification.FOLLOW
            )
        
        return FollowUser(ok=True, follow=follow_obj)


class UnfollowUser(graphene.Mutation):
    ok = graphene.Boolean()

    class Arguments:
        user_id = graphene.String(required=True)  # Changed from Int to String

    @login_required
    def mutate(self, info, user_id):
        follower = info.context.user
        
        # Convert Relay global ID to numeric ID
        numeric_user_id = get_node_id_from_global_id(user_id)
        
        if not numeric_user_id:
            raise Exception(f"Invalid user ID: {user_id}")
        
        try:
            following = User.objects.get(pk=numeric_user_id)
        except User.DoesNotExist:
            raise Exception("User not found")

        deleted_count, _ = Follow.objects.filter(
            follower=follower,
            following=following
        ).delete()
        
        return UnfollowUser(ok=deleted_count > 0)


class MarkNotificationRead(graphene.Mutation):
    ok = graphene.Boolean()
    notification = graphene.Field(NotificationNode)

    class Arguments:
        notification_id = graphene.String(required=True)  # Changed from Int to String

    @login_required
    def mutate(self, info, notification_id):
        user = info.context.user
        
        # Convert Relay global ID to numeric ID
        numeric_notification_id = get_node_id_from_global_id(notification_id)
        
        if not numeric_notification_id:
            raise Exception(f"Invalid notification ID: {notification_id}")
        
        try:
            notification = Notification.objects.get(pk=numeric_notification_id, recipient=user)
            notification.is_read = True
            notification.save()
            return MarkNotificationRead(ok=True, notification=notification)
        except Notification.DoesNotExist:
            raise Exception("Notification not found")


class Mutation(graphene.ObjectType):
    token_auth = TokenAuth.Field()
    refresh_token = graphql_jwt.Refresh.Field()
    verify_token = graphql_jwt.Verify.Field()

    create_user = CreateUser.Field()
    create_post = CreatePost.Field()
    create_comment = CreateComment.Field()
    react_to_post = ReactToPost.Field()
    follow_user = FollowUser.Field()
    unfollow_user = UnfollowUser.Field()
    mark_notification_read = MarkNotificationRead.Field()


# -----------------------------
# Schema
# -----------------------------
schema = graphene.Schema(query=Query, mutation=Mutation)