import json, requests

with open(r"C:\Users\bs00b\Desktop\実験\メルカリ受注\config.json", encoding="utf-8") as f:
    cfg = json.load(f)

query = """
{
  __schema {
    types {
      name
      fields {
        name
      }
    }
  }
}
"""

r = requests.post(
    cfg["api_endpoint"],
    headers={
        "Authorization": f"Bearer {cfg['bearer_token']}",
        "User-Agent": cfg["user_agent"],
        "Content-Type": "application/json",
    },
    json={"query": query},
    timeout=30,
)

with open(r"C:\Users\bs00b\Desktop\実験\schema.json", "w", encoding="utf-8") as f:
    json.dump(r.json(), f, ensure_ascii=False, indent=2)

print("完了: C:\\Users\\bs00b\\Desktop\\実験\\schema.json")