from django.conf import settings
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL


# ---------------------------------------------------------
# PROFILE
# ---------------------------------------------------------
class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    display_name = models.CharField(max_length=255, blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    avatar_url = models.URLField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.display_name or str(self.user)


# ---------------------------------------------------------
# POST
# ---------------------------------------------------------
class Post(models.Model):
    VISIBILITY_CHOICES = [
        ('public', 'Public'),
        ('followers', 'Followers'),
        ('private', 'Private')
    ]

    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')
    content = models.TextField()
    language = models.CharField(max_length=16, blank=True, null=True)
    visibility = models.CharField(max_length=16, choices=VISIBILITY_CHOICES, default='public')
    is_deleted = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    comment_count = models.PositiveIntegerField(default=0)
    like_count = models.PositiveIntegerField(default=0)

    # for reply threads
    reply_to = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='replies'
    )

    class Meta:
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['author', 'created_at']),
            models.Index(fields=['is_deleted']),
        ]

    def __str__(self):
        return f"Post {self.pk} by {self.author}"


# ---------------------------------------------------------
# COMMENT
# ---------------------------------------------------------
class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField()
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['post', '-created_at'])
        ]

    def __str__(self):
        return f"Comment {self.pk} on Post {self.post_id}"


# ---------------------------------------------------------
# REACTION
# ---------------------------------------------------------
class Reaction(models.Model):
    LIKE = 'like'
    REACTION_CHOICES = [(LIKE, 'Like')]

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    type = models.CharField(max_length=32, choices=REACTION_CHOICES)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ('post', 'user', 'type')
        indexes = [
            models.Index(fields=['post'])
        ]

    def __str__(self):
        return f"{self.type} by {self.user} on {self.post_id}"


# ---------------------------------------------------------
# MEDIA
# ---------------------------------------------------------
class Media(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='media')
    url = models.URLField()
    media_type = models.CharField(max_length=32, blank=True, null=True)
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Media {self.pk} for Post {self.post_id}"


# ---------------------------------------------------------
# FOLLOW
# ---------------------------------------------------------
class Follow(models.Model):
    follower = models.ForeignKey(User, related_name='following', on_delete=models.CASCADE)
    following = models.ForeignKey(User, related_name='followers', on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ('follower', 'following')
        indexes = [
            models.Index(fields=['follower']),
            models.Index(fields=['following']),
        ]

    def __str__(self):
        return f"{self.follower} -> {self.following}"


# ---------------------------------------------------------
# HASHTAG
# ---------------------------------------------------------
class Hashtag(models.Model):
    tag = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['tag'])
        ]

    def __str__(self):
        return f"#{self.tag}"


# ---------------------------------------------------------
# POST–HASHTAG RELATION
# ---------------------------------------------------------
class PostHashtag(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='post_hashtags')
    hashtag = models.ForeignKey(Hashtag, on_delete=models.CASCADE, related_name='tag_posts')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ('post', 'hashtag')
        indexes = [
            models.Index(fields=['post']),
            models.Index(fields=['hashtag']),
        ]

    def __str__(self):
        return f"{self.post_id} -> #{self.hashtag.tag}"


# ---------------------------------------------------------
# NOTIFICATION
# ---------------------------------------------------------
class Notification(models.Model):
    LIKE = 'like'
    COMMENT = 'comment'
    FOLLOW = 'follow'

    TYPES = [
        (LIKE, 'Like'),
        (COMMENT, 'Comment'),
        (FOLLOW, 'Follow'),
    ]

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    actor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='actions')
    type = models.CharField(max_length=32, choices=TYPES)
    post = models.ForeignKey(Post, null=True, blank=True, on_delete=models.CASCADE)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['recipient']),
            models.Index(fields=['is_read']),
        ]

    def __str__(self):
        return f"Notify {self.recipient} about {self.type}"
