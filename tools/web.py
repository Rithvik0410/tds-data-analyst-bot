"""
Free web tools: DuckDuckGo HTML search (no API key required) and a generic
URL fetcher that returns cleaned text (or raw bytes info for binary files
like CSV/XLS so the agent knows to fetch+parse them via python_exec).
"""
import time
import requests
from bs4 import BeautifulSoup

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search DuckDuckGo's HTML endpoint and return [{title, url, snippet}]."""
    url = "https://html.duckduckgo.com/html/"
    for attempt in range(3):
        try:
            resp = requests.post(
                url,
                data={"q": query},
                headers={"User-Agent": _UA},
                timeout=15,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            results = []
            for res in soup.select(".result")[:max_results]:
                link = res.select_one(".result__a")
                snippet = res.select_one(".result__snippet")
                if not link:
                    continue
                results.append({
                    "title": link.get_text(strip=True),
                    "url": link.get("href", ""),
                    "snippet": snippet.get_text(strip=True) if snippet else "",
                })
            if results:
                return results
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    return []


def web_fetch(url: str, max_chars: int = 8000) -> dict:
    """Fetch a URL. Returns cleaned text for HTML, or metadata + a note for
    binary/tabular files (CSV/XLS/XLSX) so the agent downloads them inside
    python_exec instead (where pandas can parse them properly)."""
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=25)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"url": url, "error": str(e)}

    content_type = resp.headers.get("Content-Type", "")

    if any(t in content_type for t in ["csv", "excel", "spreadsheet", "octet-stream"]) \
            or url.lower().endswith((".csv", ".xls", ".xlsx")):
        return {
            "url": url,
            "content_type": content_type,
            "note": (
                "This looks like a tabular data file. Don't parse it as "
                "text -- use python_exec with "
                "pandas.read_csv/read_excel(url) to load and analyze it."
            ),
        }

    if "html" in content_type or resp.text.strip().startswith("<"):
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        return {"url": url, "content_type": content_type, "text": text[:max_chars]}

    return {"url": url, "content_type": content_type, "text": resp.text[:max_chars]}
