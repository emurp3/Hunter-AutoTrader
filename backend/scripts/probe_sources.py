import json

import httpx


def main() -> None:
    candidates = [
        ("hn_jobs", "https://hn.algolia.com/api/v1/search_by_date?tags=job,story&query=automation&hitsPerPage=5"),
        ("hn_stories", "https://hn.algolia.com/api/v1/search?query=need%20help%20automation&tags=story&hitsPerPage=5"),
        ("remoteok", "https://remoteok.com/api"),
        ("remotive", "https://remotive.com/api/remote-jobs?search=automation"),
        ("wwe_remotely_rss", "https://weworkremotely.com/remote-jobs.rss"),
        ("stackexchange", "https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=creation&q=need+help+automation&site=stackoverflow&pagesize=5"),
        ("overpass", "https://overpass-api.de/api/interpreter?data=[out:json];node[\"amenity\"=\"dentist\"](40.7128,-74.0060,40.7528,-73.9660);out%20tags%205;"),
        ("grants_gov", "https://www.grants.gov/grantsws/rest/opportunities/search/?keyword=technology"),
        ("affpaying", "https://www.affpaying.com/"),
        (
            "shopgoodwill_api",
            "https://buyerapi.shopgoodwill.com/api/ItemListing/GetItemsByKeywords?keyWords=dyson&page=1&pageSize=5&sortColumn=1&sortDirection=0&selectedCategoryIds=&selectedSellerIds=&lowPrice=0&highPrice=0&searchBuyNowOnly=false&searchPickupOnly=false",
        ),
        (
            "github_repos",
            "https://api.github.com/search/repositories?q=automation+agent+stars:%3E20&sort=updated&order=desc&per_page=5",
        ),
        (
            "github_issues",
            'https://api.github.com/search/issues?q=%22need+help%22+automation+state:open&sort=updated&order=desc&per_page=5',
        ),
        ("ebay_rss", "https://www.ebay.com/sch/i.html?_nkw=dyson&_rss=1"),
        (
            "slickdeals_rss",
            "https://slickdeals.net/newsearch.php?q=dyson&searcharea=deals&searchin=first&rss=1",
        ),
        ("rssing_search", "https://www.rssing.com/index.php?zw=1&q=automation"),
        (
            "reddit_json",
            "https://www.reddit.com/r/smallbusiness/search.json?q=need%20help&restrict_sr=1&sort=new&limit=5",
        ),
    ]
    headers = {
        "User-Agent": "HunterSourceAcquisition/0.3",
        "Accept": "application/json, application/xml, text/xml, */*",
    }

    with httpx.Client(timeout=15.0, headers=headers, follow_redirects=True) as client:
        for name, url in candidates:
            result = {"name": name, "url": url}
            try:
                resp = client.get(url)
                text = resp.text
                ctype = (resp.headers.get("content-type") or "").lower()
                result.update(
                    {
                        "status_code": resp.status_code,
                        "content_type": resp.headers.get("content-type"),
                        "extractable": False,
                        "signal_quality": "weak",
                        "snippet": text[:240].replace("\n", " "),
                    }
                )
                if "application/json" in ctype or text.strip().startswith(("{", "[")):
                    data = resp.json()
                    result["response_format"] = "json"
                    if isinstance(data, dict):
                        result["top_keys"] = list(data.keys())[:12]
                        if any(key in data for key in ("items", "hits", "total_count")):
                            result["extractable"] = True
                            result["signal_quality"] = "strong"
                    elif isinstance(data, list):
                        result["response_format"] = "json_list"
                        result["list_len"] = len(data)
                        if len(data) > 1 and isinstance(data[1], dict):
                            result["top_keys"] = list(data[1].keys())[:12]
                            result["extractable"] = True
                            result["signal_quality"] = "strong"
                elif "xml" in ctype or "<rss" in text[:500].lower():
                    result["response_format"] = "rss/xml"
                    result["extractable"] = "<item>" in text.lower()
                    result["signal_quality"] = "medium" if result["extractable"] else "weak"
                else:
                    result["response_format"] = "html/other"
                print(json.dumps(result))
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"name": name, "url": url, "error": repr(exc)}))


if __name__ == "__main__":
    main()
