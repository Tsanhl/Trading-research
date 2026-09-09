"""Bounded public RSS ingestion; no login bypass or completeness claims."""
from __future__ import annotations

import hashlib
import subprocess
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from urllib.error import HTTPError
from xml.etree import ElementTree as ET

from .common import ROOT, now_iso, save_json

HOST = "edgerunner17888.substack.com"
FEED = "https://"+HOST+"/feed"
AGENT = "TradingResearchHub/0.1 (private personal research)"


def fetch(url):
    if urlsplit(url).hostname != HOST or urlsplit(url).scheme != "https":
        raise ValueError("Source not allowlisted")
    # System curl uses the macOS trust store. Do not disable TLS verification to
    # work around standalone Python installations without CA certificates.
    response = subprocess.run(["/usr/bin/curl","--silent","--show-error","--max-time","15",
                               "--max-filesize","2000000","--proto","=https","--max-redirs","0",
                               "--user-agent",AGENT,"--write-out","\n%{http_code}",url],
                              capture_output=True,timeout=20)
    if response.returncode:
        raise RuntimeError("Bounded HTTPS fetch failed")
    data,status = response.stdout.rsplit(b"\n",1)
    if status != b"200":
        raise HTTPError(url,int(status),"HTTP status refused; redirects are not followed",{},None)
    if len(data)>2_000_000:
        raise ValueError("Public response exceeds bounded crawl size")
    return data


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script","style","noscript"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script","style","noscript"}:
            self.skip = max(0,self.skip-1)
        if tag in {"p","div","li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def crawl_edgerunner(fetcher=fetch):
    result = {"source":FEED, "fetched_at":now_iso(), "status":"BOUNDED", "full_history":False,
              "documents":[], "content_is_untrusted_data":True}
    try:
        robot = RobotFileParser()
        try:
            robots = fetcher("https://"+HOST+"/robots.txt")
            robot.parse(robots.decode("utf-8").splitlines())
            if not robot.can_fetch(AGENT,FEED):
                result["reason"] = "ROBOTS_DISALLOWED"
                return result
        except HTTPError as error:
            if error.code != 404:
                raise
        raw = fetcher(FEED)
        result["response_sha256"] = hashlib.sha256(raw).hexdigest()
        root = ET.fromstring(raw)
        for item in root.findall("./channel/item")[:50]:
            url = item.findtext("link","")
            if urlsplit(url).hostname != HOST or urlsplit(url).scheme != "https":
                continue
            body = item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or item.findtext("description","")
            parser = PlainText()
            parser.feed(body)
            text = "".join(parser.parts).strip()
            sha = hashlib.sha256(text.encode()).hexdigest()
            record = {"url":url, "title":item.findtext("title",""), "published_at":item.findtext("pubDate",""),
                      "known_at":result["fetched_at"], "content_sha256":sha,
                      "coverage":"FEED_CONTENT_NOT_VERIFIED_FULL_ARTICLE", "text":text}
            result["documents"].append(record)
        result["status"] = "PUBLIC_FEED_CAPTURED"
    except Exception as error:
        result["reason"] = type(error).__name__  # Never log arbitrary response/credential text.
    return result


def save_crawl(result):
    identity = result.get("response_sha256") or hashlib.sha256(result["fetched_at"].encode()).hexdigest()
    path = ROOT/"state/research/edgerunner"/(identity+".json")
    if not path.exists():
        save_json(path,result)
    save_json(ROOT/"state/research/latest-crawl.json",{k:v for k,v in result.items() if k != "documents"} | {"document_count":len(result["documents"]),"artifact":str(path)})
    return path
