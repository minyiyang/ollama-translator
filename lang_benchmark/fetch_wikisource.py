"""Fetch a public-domain text from Chinese Wikisource as Simplified Chinese plain text.

    python lang_benchmark/fetch_wikisource.py "阿Q正傳" out.txt

Wikisource stores this text in Traditional Chinese; `variant=zh-hans` asks the
site for its Simplified conversion. Paragraphs are separated by blank lines.
"""
import html, json, re, sys, time, urllib.parse, urllib.request

API = "https://zh.wikisource.org/w/api.php"
AGENT = "ollama-translator-benchmark/0.1 (local research; one-off fetch)"


def call(**params):
    query = urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    request = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def page_text(title):
    data = call(action="parse", page=title, prop="text", variant="zh-hans", disablelimitreport="1", disableeditsection="1")
    if "error" in data:
        raise SystemExit(f"{title}: {data['error'].get('info')}")
    body = data["parse"]["text"]
    body = re.sub(r"<(style|script|table)[^>]*>.*?</\1>", "", body, flags=re.S)
    blocks = re.findall(r"<(p|h[1-6]|dd)[^>]*>(.*?)</\1>", body, flags=re.S)
    lines = []
    for _, block in blocks:
        text = html.unescape(re.sub(r"<[^>]+>", "", block)).strip()
        text = re.sub(r"\[\d+\]", "", text)
        if text:
            lines.append(text)
    return lines


if __name__ == "__main__":
    title, output = sys.argv[1], sys.argv[2]
    info = call(action="parse", page=title, prop="links|wikitext")
    if "error" in info:
        raise SystemExit(info["error"].get("info"))
    subpages = [link["title"] for link in info["parse"]["links"] if link["title"].startswith(title + "/")]
    print("subpages:", subpages or "(none: single page)", "| wikitext", len(info["parse"]["wikitext"]))
    parts = []
    for name in subpages or [title]:
        lines = page_text(name)
        print(f"  {name}: {len(lines)} paragraphs, {sum(map(len, lines))} characters; first: {lines[0][:40] if lines else ''}")
        parts.append((name, lines))
        time.sleep(1)
    with open(output, "w", encoding="utf-8", newline="\n") as handle:
        for name, lines in parts:
            handle.write("\n\n".join(lines) + "\n\n")
