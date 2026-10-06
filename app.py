from datetime import datetime, timedelta
import re
from bs4 import BeautifulSoup
from notion_client import Client
import requests
import streamlit as st

# ==========================================
# 1. 노션 설정 로드 (Streamlit Secrets & 상수)
# ==========================================
NOTION_TOKEN = st.secrets["NOTION_TOKEN"]
DATABASE_ID = st.secrets["DATABASE_ID"]
CATEGORY_RELATION_ID = "e409fb1b1f8982df81928157b6bd5374"

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
# 2. 최근 30건 중 '유상증자' 공시 수신 & 제목 정제
# ==========================================
def fetch_naver_notices(stock_code: str):
    url = (
        f"https://stock.naver.com/api/domestic/detail/notice"
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
            date_part = raw_datetime.split("T")[0]
            date_str = date_part.replace("-", ".")

        corp_name = re.split(r"\(정정\)|유상증자", title)[0].
