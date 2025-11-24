# Project NEXUS

## Overview

This document provides a developer-friendly guide on how to interact with the GraphQL API for this social application. It includes schema structure, common queries, mutations, authentication, and testing instructions using the GraphQL Playground.

## 🚀 Features

    - Relay-compliant GraphQL API

    - JWT Authentication (Login + Protected Mutations)

    - Create Posts & Comments

    - Follow/Unfollow Users

    - Fetch Timeline Feed

    - Relay Node and Connection Support

## 🏗️ GraphQL Schema Structure

    - The API is built using:

    - Graphene-Django

    - Relay Node Interface

    - DjangoFilterConnectionField for pagination & filtering

## Main Node Types

    - UserNode

    - ProfileNode

    - PostNode

    - CommentNode

    - FollowNode

    - TimelineNode

## 🔐 Authentication

The API uses **JWT authentication**. Obtain a token via:

```
mutation {
  createUser(username: "your_username", password: "your_password") {
    user {
      id
    }
  }
}
```

```
mutation {
  tokenAuth(username: "your_username", password: "your_password") {
    token
  }
}
```

### Adding JWT in GraphQL Playground

In **HTTP Headers**:

```
{
  "Authorization": "JWT <your-token>"
}
```

---

## 📌 Common Queries

### Fetch authenticated user

```
query {
  me {
    id
    username
  }
}
```

### Fetch all posts

```
query {
  allPosts(first: 20) {
    edges {
      node {
        id
        content
        visibility
        author {
          username
        }
      }
    }
  }
}
```

### Fetch single post by ID

```
query {
  node(id: "<GLOBAL_POST_ID>") {
    ... on PostNode {
      id
      content
      author {
        username
      }
    }
  }
}
```

### Fetch comments for a post

```
query {
  allComments(postId: "<GLOBAL_POST_ID>") {
    edges {
      node {
        id
        content
        author {
          username
        }
      }
    }
  }
}
```

### Fetch user profile

```
query {
  allProfiles(displayName_Icontains: "john") {
    edges {
      node {
        id
        displayName
        bio
        user {
          username
        }
      }
    }
  }
}
```

---

## 📝 Mutations

### Follow a user

```
mutation {
  followUser(input: { userId: "<GLOBAL_USER_ID>" }) {
    ok
    follow {
      id
      follower {
        username
      }
      following {
        username
      }
    }
  }
}
```

### Create a comment on a post

```
mutation {
  createComment(
    input: {
      postId: "<GLOBAL_POST_ID>"
      content: "Nice post!"
    }
  ) {
    comment {
      id
      content
      author {
        username
      }
    }
  }
}
```

---

## 📰 Timeline (Feed)

Fetch timeline entries for the logged-in user:

```
query {
  allTimeline(first: 20) {
    edges {
      node {
        post {
          content
          author {
            username
          }
        }
        createdAt
      }
    }
  }
}
```

---

## 🧪 Testing With GraphQL Playground

1. Start Django server:

```
python manage.py runserver
```

2. Navigate to:

```
http://localhost:8000/graphql/
```

3. Use the **Docs** and **Schema** tab to browse the API.
4. Add the JWT token inside the **HTTP HEADERS** tab.

---

## 📂 Project Structure (GraphQL)

```
schema.py   # GraphQL nodes, queries, mutations
models.py   # Django ORM models
urls.py     # GraphQL endpoint routing
```

---

## 📘 Best Practices

* Always request only needed fields to reduce load
* Use Relay pagination (`first`, `after`)
* Keep mutations protected using `@login_required`
* Use global IDs for Relay operations

---