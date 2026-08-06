from openai import OpenAI

client = OpenAI()

response = client.responses.create(
    model="gpt-5",
    input="Antworte nur mit: API-Verbindung funktioniert.",
)

print(response.output_text)