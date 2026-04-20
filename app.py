import hashlib
import logging
import os
from typing import Any, Dict, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)
LANGUAGE_CODE_MAP = {
    "english": "en",
    "en": "en",
    "chinese": "zh",
    "zh": "zh",
    "spanish": "es",
    "es": "es",
    "french": "fr",
    "fr": "fr",
    # 阿拉伯
    "arabic": "ar",
    "ar": "ar",
    # 俄语
    "russian": "ru",
    "ru": "ru",
     # 日语
    "japanese": "ja",
    "ja": "ja",
}

def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


app.config["DATABASE_URL"] = os.getenv("DATABASE_URL", "")
app.config["AI_API_URL"] = os.getenv("AI_API_URL", "")
app.config["AI_API_KEY"] = os.getenv("AI_API_KEY", "")
app.config["AI_MODEL"] = os.getenv("AI_MODEL", "")
app.config["AI_USE_THINKING"] = parse_bool(os.getenv("AI_USE_THINKING", "false"))
app.config["AI_TIMEOUT_SECONDS"] = int(os.getenv("AI_TIMEOUT_SECONDS", "30"))


def get_db_connection() -> psycopg2.extensions.connection:
    database_url = app.config["DATABASE_URL"]
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return psycopg2.connect(database_url)


def init_db() -> None:
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS translation_cache (
        id BIGSERIAL PRIMARY KEY,
        text_md5 CHAR(32) NOT NULL,
        source_text TEXT NOT NULL,
        target_language VARCHAR(32) NOT NULL,
        translated_text TEXT NOT NULL,
        model_name VARCHAR(128),
        use_thinking BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (text_md5, target_language)
    );
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(create_table_sql)
        conn.commit()


def text_to_md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def get_cached_translation(text_md5: str, target_language: str) -> Optional[Dict[str, Any]]:
    query_sql = """
    SELECT text_md5, source_text, target_language, translated_text, model_name, use_thinking
    FROM translation_cache
    WHERE text_md5 = %s AND target_language = %s
    LIMIT 1;
    """

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query_sql, (text_md5, target_language))
            row = cur.fetchone()
            return dict(row) if row else None


def save_translation(
    text_md5: str,
    source_text: str,
    target_language: str,
    translated_text: str,
    model_name: str,
    use_thinking: bool,
) -> None:
    upsert_sql = """
    INSERT INTO translation_cache (
        text_md5, source_text, target_language, translated_text, model_name, use_thinking
    )
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (text_md5, target_language)
    DO UPDATE SET
        source_text = EXCLUDED.source_text,
        translated_text = EXCLUDED.translated_text,
        model_name = EXCLUDED.model_name,
        use_thinking = EXCLUDED.use_thinking,
        updated_at = NOW();
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                upsert_sql,
                (text_md5, source_text, target_language, translated_text, model_name, use_thinking),
            )
        conn.commit()


def extract_text_from_ai_response(data: Dict[str, Any]) -> str:
    if isinstance(data.get("translation"), str) and data["translation"].strip():
        return data["translation"].strip()

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else None

        if isinstance(content, str) and content.strip():
            return content.strip()

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(item, str):
                    parts.append(item)
            merged = "".join(parts).strip()
            if merged:
                return merged

    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    raise ValueError("Unable to parse translated text from AI response.")


def call_ai_translate(text: str, target_language: str) -> str:
    ai_api_url = app.config["AI_API_URL"]
    ai_api_key = app.config["AI_API_KEY"]
    ai_model = app.config["AI_MODEL"]
    ai_use_thinking = app.config["AI_USE_THINKING"]

    if not ai_api_url:
        raise RuntimeError("AI_API_URL is not configured.")
    if not ai_api_key:
        raise RuntimeError("AI_API_KEY is not configured.")
    if not ai_model:
        raise RuntimeError("AI_MODEL is not configured.")

    payload: Dict[str, Any] = {
        "model": ai_model,
        "messages": [
            {
                "role": "system",
                "content": "You are a deterministic translation function.\n\nRules:\n1. Output = translation(input)\n2. No extra tokens before or after\n3. No explanations\n4. No formatting\n5. No markdown\n6. No metadata\n\nOnly return the translated string.",
            },
            {
                "role": "user",
                "content": (
                    f"Translate the following text into {target_language}. "
                    f"Return ONLY the translation.\n"
                    f"Text:\n{text}"
                ),
            },
        ],
        "temperature": 0,
    }

    if ai_use_thinking:
        payload["thinking"] = True
        payload["reasoning"] = {"effort": "medium"}

    headers = {
        "Authorization": f"Bearer {ai_api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        ai_api_url,
        headers=headers,
        json=payload,
        timeout=app.config["AI_TIMEOUT_SECONDS"],
    )
    response.raise_for_status()

    response_data = response.json()
    return extract_text_from_ai_response(response_data)


@app.route("/api/translate", methods=["POST"])
def translate() -> Any:
    payload = request.get_json(silent=True) or {}

    text = payload.get("text")
    target_language = payload.get("target_language")

    if not isinstance(text, str) or not text.strip():
        return jsonify({"message": "text must be a non-empty string."}), 400

    if not isinstance(target_language, str) or not target_language.strip():
        return jsonify({"message": "target_language must be a non-empty string."}), 400

    text = text.strip()
    target_language = target_language.strip().lower()
    text_md5 = text_to_md5(text)

    target_language = LANGUAGE_CODE_MAP.get(target_language.lower(), target_language)

    try:
        cached = get_cached_translation(text_md5, target_language)
        if cached:
            return jsonify(
                {
                    "text_md5": text_md5,
                    "target_language": target_language,
                    "translated_text": cached["translated_text"],
                    "from_cache": True,
                }
            )

        translated_text = call_ai_translate(text, target_language)

        save_translation(
            text_md5=text_md5,
            source_text=text,
            target_language=target_language,
            translated_text=translated_text,
            model_name=app.config["AI_MODEL"],
            use_thinking=app.config["AI_USE_THINKING"],
        )

        return jsonify(
            {
                "text_md5": text_md5,
                "target_language": target_language,
                "translated_text": translated_text,
                "from_cache": False,
            }
        )
    except requests.RequestException as exc:
        logger.exception("AI request failed: %s", exc)
        return jsonify({"message": "AI request failed.", "detail": str(exc)}), 502
    except psycopg2.Error as exc:
        logger.exception("Database error: %s", exc)
        return jsonify({"message": "Database operation failed.", "detail": str(exc)}), 500
    except Exception as exc:
        logger.exception("Unhandled server error: %s", exc)
        return jsonify({"message": "Server error.", "detail": str(exc)}), 500

def translate_text_lingva(text: str, target_language: str = "ar") -> str:
    """Send text to translation API and return translated text."""
    url = f"https://translate.hzchu.top/api/v1/auto/{target_language}/{text}"

    response = requests.get(url)
    print(response.text)
    result = response.json()
    
    return result.get("translation", "")


def create_app() -> Flask:
    init_db()
    return app


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
