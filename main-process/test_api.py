import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

client = OpenAI(
    api_key=os.environ["ACADEMIC_CLOUD_API_KEY"],
    base_url=os.environ.get(
        "ACADEMIC_CLOUD_API_ENDPOINT",
        "https://chat-ai.academiccloud.de/v1",
    ),
)

response = client.chat.completions.create(
    model=os.environ.get("ENRICHMENT_MODEL", "qwen3.6-35b-a3b"),
    messages=[
        {
            "role": "system",
            "content": "You are a BIDS knowledge engineer."
        },
        {
            "role": "user",
            "content": "Explain this BIDS error..."
        }
    ],
    temperature=0.2
)

answer = response.choices[0].message.content

print(answer)
