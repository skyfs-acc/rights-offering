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
# 2. 공시 조회 함수
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

        raw_dt = item.get("datetime", "")
        date_str = ""
        if raw_dt:
            date_str = raw_dt.split("T")[0].replace("-", ".")

        corp_match = re.split(r"\(정정\)|유상증자", title)
        corp_name = title
        if corp_match and corp_match[0]:
            corp_name = corp_match[0].strip()

        filtered_list.append({
            "title": title,
            "corp_name": corp_name,
            "no": str(item.get("no", "")),
            "date": date_str,
            "contents": item.get("contents", ""),
        })

    return filtered_list

# ==========================================
# 3. 본문 파싱 함수
# ==========================================
def parse_offering_schedule_from_contents(html_content: str):
    if not html_content:
        return {}

    soup = BeautifulSoup(html_content, "html.parser")
    text = soup.get_text()

    def to_standard_date(raw_str):
        if not raw_str:
            return ""
        c = (
            raw_str.replace("년", "-")
            .replace("월", "-")
            .replace("일", "")
            .replace(".", "-")
            .replace(" ", "")
        )
        p = c.split("-")
        if len(p) >= 3:
            if p[0].isdigit() and p[1].isdigit() and p[2].isdigit():
                return f"{p[0]}/{int(p[1]):02d}/{int(p[2]):02d}"
        return ""

    def find_date(keywords):
        date_pat = r"(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})"
        for kw in keywords:
            pattern = rf"{kw}[^\d]{{0,50}}{date_pat}"
            m = re.search(pattern, text)
            if m:
                return to_standard_date(m.group(1))
        return ""

    # 1. 신주배정기준일 및 권리락일
    record_date = find_date(["신주배정기준일", "배정기준일"])
    ex_rights_date = ""
    if record_date:
        try:
            dt = datetime.strptime(record_date, "%Y/%m/%d")
            dt_prev = dt - timedelta(days=1)
            while dt_prev.weekday() >= 5:
                dt_prev -= timedelta(days=1)
            ex_rights_date = dt_prev.strftime("%Y/%m/%d")
        except Exception:
            pass

    # 2. 신주 발행가액
    issue_price = ""
    m_cf = re.search(
        r"확정발행가[^\d]{0,40}보통주식[^\d]{0,30}([\d,]+)",
        text,
    )
    if m_cf and m_cf.group(1).replace(",", "").isdigit():
        issue_price = f"{int(m_cf.group(1).replace(',', '')):,}"

    if not issue_price:
        m_ep = re.search(
            r"예정발행가[^\d]{0,40}보통주식[^\d]{0,30}([\d,]+)",
            text,
        )
        if m_ep and m_ep.group(1).replace(",", "").isdigit():
            issue_price = f"{int(m_ep.group(1).replace(',', '')):,}"

    if not issue_price:
        m_fb = re.search(
            r"(?:신주발행가액|발행가액)[^\d]{0,40}([\d,]+)\s*원",
            text,
        )
        if m
