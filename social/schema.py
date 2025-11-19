import graphene
from graphene_django import DjangoObjectType
from graphene_django.filter import DjangoFilterConnectionField
import graphql_jwt
from graphql_jwt.decorators import login_required
from .models import Post, Comment, Reaction, Profile, Follow, Hashtag, PostHashtag, Notification

class PostType(DjangoObjectType):
    class Meta:
        model = Post
        filter_fields = ['author__id', 'visibility', 'created_at']
        interfaces = (graphene.relay.Node,)

class CommentType(DjangoObjectType):
    class Meta:
        model = Comment
        filter_fields = ['author__id', 'post__id', 'created_at']
        interfaces = (graphene.relay.Node,)

class Query(graphene.ObjectType):
    all_posts = DjangoFilterConnectionField(PostType)
    post = graphene.Field(PostType, id=graphene.Int())

    @login_required
    def resolve_all_posts(self, info, **kwargs):
        return Post.objects.filter(is_deleted=False).order_by('-created_at')

    @login_required
    def resolve_post(self, info, id):
        try:
            return Post.objects.get(pk=id, is_deleted=False)
        except Post.DoesNotExist:
            return None

class CreatePost(graphene.Mutation):
    post = graphene.Field(PostType)

    class Arguments:
        content = graphene.String(required=True)
        visibility = graphene.String(required=False)

    @login_required
    def mutate(self, info, content, visibility='public'):
        user = info.context.user
        post = Post.objects.create(author=user, content=content, visibility=visibility)
        return CreatePost(post=post)

class CreateComment(graphene.Mutation):
    comment = graphene.Field(CommentType)

    class Arguments:
        post_id = graphene.Int(required=True)
        content = graphene.String(required=True)
    
    @login_required
    def mutate(self, info, post_id, content):
        user = info.context.user
        post = Post.objects.get(pk=post_id)
        comment = Comment.objects.create(author=user, post=post, content=content)
        post.comment_count = post.comments.count()
        post.save()
        return CreateComment(comment=comment)

class DebugUser(graphene.Mutation):
    user = graphene.String()

    def mutate(self, info):
        return DebugUser(user=str(info.context.user))

class Mutation(graphene.ObjectType):
    debug_user = DebugUser.Field()
    create_post = CreatePost.Field()
    create_comment = CreateComment.Field()
    token_auth = graphql_jwt.ObtainJSONWebToken.Field()
    

schema = graphene.Schema(query=Query, mutation=Mutation)
