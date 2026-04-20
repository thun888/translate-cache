# translate-cache

A Flask translation cache service.

## Features

- Accepts source text and target language.
- Uses MD5 of source text for cache lookup.
- Reads/writes translation cache in PostgreSQL.
- Falls back to AI service when cache is missing.

## API

### POST /api/translate

Request body:

```json
{
  "text": "Hello, world",
  "target_language": "en"
}
```

Response body:

```json
{
  "text_md5": "6cd3556deb0da54bca060b4c39479839",
  "target_language": "en",
  "translated_text": "Hello, world",
  "from_cache": false
}
```

## Run

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and update values.

3. Start service:

```bash
python app.py
```
