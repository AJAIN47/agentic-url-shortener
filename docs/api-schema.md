# API Schema

## Create a short link

Request:

```http
POST /api/links
Content-Type: application/json
```

Body:

```json
{
  "url": "https://example.com/docs",
  "token": "demo123",
  "ttl_seconds": 3600
}
```

Response `201`:

```json
{
  "token": "demo123",
  "url": "https://example.com/docs",
  "created_at": 1700000000.0,
  "expires_at": 1700003600.0,
  "short_url": "http://127.0.0.1:8080/demo123"
}
```

## Redirect a short link

Request:

```http
GET /{token}
```

Response `302` with `Location` header pointing to the original URL.

## Get analytics

Request:

```http
GET /api/links/{token}/stats
```

Response `200`:

```json
{
  "token": "demo123",
  "url": "https://example.com/docs",
  "created_at": 1700000000.0,
  "expires_at": 1700003600.0,
  "clicks": 3
}
```

## Error responses

Common error payloads:

```json
{
  "error": "url must be an absolute http or https URL"
}
```

and

```json
{
  "error": "rate limit exceeded: too many requests from this client"
}
```
