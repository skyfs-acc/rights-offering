import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from notion_client import Client
import requests
import streamlit as st

# ==========================================
# 1. 노션 설정
# ==========================================
NOTION_TOKEN = st.secrets["NOTION_TOKEN"]
DATABASE_ID = st.secrets["DATABASE_ID"]
CATEGORY_RELATION_ID = (
    "e409fb1b1f8982df81928157b6bd5374"
)

notion = Client(auth=NOTION_TOKEN)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Referer": "https://stock.naver.com/",
}


# ==========================================
# 2. 공시 조회
# ==========================================
def fetch_naver_notices(stock_code: str):
    url = (
        "https://stock.naver.com"
        "/api/domestic/detail/notice"
        f"?itemCode={stock_code}&startIdx=0&pageSize=30"
    )
    res = requests.get(url, headers=HEADERS, timeout=10)
    res.raise_for_status()
    data = res.json()

    notices = []
    if isinstance(data, list):
        notices = data
    elif isinstance(data, dict):
        notices = (
            data.get("notices")
            or data.get("list")
            or data.get("result")
            or []
        )

    filtered_list = []
    for item in notices:
        title = item.get("title", "")
        if "유상증자" not in title:
            continue

        notice_no = item.get("no", "")
        raw_datetime = item.get("datetime", "")
        html_contents = item.get("contents", "")

        date_str = ""
        if raw_datetime:
            parts = raw_datetime.split("T")
            date_str = parts[0].replace("-", ".")

        # 긴 체이닝을 단계별로 분리
        split_titles = re.split(r"\(정정\)|유상증자", title)
        if split_titles and split_titles[0]:
            first_elem = split_titles[0]
            corp_name = first_elem.strip()
        else:
            corp_name = title

        filtered_list.append({
            "title": title,
            "corp_name": corp_name,
            "no": str(notice_no),
            "date": date_str,
            "contents": html_contents,
        })

    return filtered_list


# ==========================================
# 3. 본문 파싱
# ==========================================
def parse_offering_schedule_from_contents(html_content: str):
    if not html_content:
        return {}

    soup = BeautifulSoup(html_content, "html.parser")
    text = soup.get_text()

    def to_standard_date(raw_str):
        if not raw_str:
            return ""
        c = raw_str
        c = c.replace("년", "-")
        c = c.replace("월", "-")
        c = c.replace("일", "")
