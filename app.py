import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from notion_client import Client
import requests
import streamlit as st

# 1. 노션 설정
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

# 2. 공시 조회 함수
def fetch_naver_notices(stock_code: str):
    url = f"https://stock.naver.com/api/domestic/detail/notice?itemCode={stock_code}&startIdx=0&pageSize=30"
    res = requests.get(url, headers=HEADERS, timeout=10)
    res.raise_for_status()
    data = res.json()

    notices = []
    if isinstance(data, list):
        notices = data
    elif isinstance(data, dict):
        notices = data.get("notices") or data.get("list") or data.get("result") or []

    filtered_list = []
    for item in notices:
        title = item.get("title", "")
        if "유상증자" not in title:
            continue

        raw_dt = item.get("datetime", "")
        date_str = raw_dt.split("T")[0].replace("-", ".") if raw_dt else ""

        corp_match = re.split(r"\(정정\)|유상증자", title)
        corp_name = corp_match[0].strip() if corp_match and corp_match[0] else title

        filtered_list.append({
            "title": title,
            "corp_name": corp_name,
            "no": str(item.get("no", "")),
            "date": date_str,
            "contents": item.get("contents", ""),
        })

    return filtered_list

# 3. 본문 일정/데이터 파싱 함수
def parse_offering_schedule_from_contents(html_content: str):
    if not html_content:
        return {}

    soup = BeautifulSoup(html_content, "html.parser")
    text = soup.get_text()

    def to_standard_date(raw_str):
        if not raw_str:
            return ""
        c = raw_str.replace("년", "-").replace("월", "-").replace("일", "").replace(".", "-").replace(" ", "")
        p = c.split("-")
        if len(p) >= 3 and p[0].isdigit() and p[1].isdigit() and p[2].isdigit():
            return f"{p[0]}/{int(p[1]):02d}/{int(p[2]):02d}"
        return ""

    def find_date(keywords):
        for kw in keywords:
            m = re.search(rf"{kw}[^\d]{{0,50}}(\d{{4}}[\.\-년]\s*\d{{1,2}}[\.\-월]\s*\d{{1,2}})", text)
            if m:
                return to_standard_date(m.group(1))
        return ""

    # 신주배정기준일 & 권리락일
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

    # 신주 발행가액
    issue_price = ""
    m_cf = re.search(r"확정발행가[^\d]{0,40}보통주식[^\d]{0,30}([\d,]+)", text)
    if m_cf and m_cf.group(1).replace(",", "").isdigit():
        issue_price = f"{int(m_cf.group(1).replace(',', '')):,}"
    if not issue_price:
        m_ep = re.search(r"예정발행가[^\d]{0,40}보통주식[^\d]{0,30}([\d,]+)", text)
        if m_ep and m_ep.group(1).replace(",", "").isdigit():
            issue_price = f"{int(m_ep.group(1).replace(',', '')):,}"
    if not issue_price:
        m_fb = re.search(r"(?:신주발행가액|발행가액)[^\d]{0,40}([\d,]+)\s*원", text)
        if m_fb and m_fb.group(1).replace(",", "").isdigit():
            issue_price = f"{int(m_fb.group(1).replace(',', '')):,}"

    # 배정비율
    applied_ratio = ""
    m_rt = re.search(r"(?:신주배정주식수|신주배정비율)[^\d]{0,30}(\d+\.\d+)", text)
    if m_rt:
        try:
            applied_ratio = f"{float(m_rt.group(1)) * 100:.11f}".rstrip("0") + "%"
        except Exception:
            applied_ratio = m_rt.group(1)

    # 신주인수권 상장/폐지일
    rights_start = ""
    rights_end = ""
    m_rg = re.search(r"신주인수권[^\d]{0,30}(\d{4}[년\.\-]\s*\d{1,2}[월\.\-]\s*\d{1,2}일?)\s*(?:~|-)\s*(\d{4}[년\.\-]\s*\d{1,2}[월\.\-]\s*\d{1,2}일?)", text)
    if m_rg:
        rights_start = to_standard_date(m_rg.group(1))
        rights_end = to_standard_date(m_rg.group(2))
    if not rights_start:
        rights_start = find_date(["신주인수권증서 상장예정일", "신주인수권증서 상장일", "신주인수권 상장예정일", "신주인수권 상장일"])

    # 구주주 청약 시작일 / 종료일
    sub_start_date = ""
    sub_end_date = ""
    for tr in soup.find_all("tr"):
        tr_text = tr.get_text()
        if "구주주" in tr_text and not sub_start_date:
            dates = re.findall(r"(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})", tr_text)
            if dates:
                sub_start_date = to_standard_date(dates[0])
                if len(dates) > 1:
                    sub_end_date = to_standard_date(dates[1])
                break

    if not sub_start_date:
        sub_start_date = find_date(["구주주청약일", "구주주청약", "청약예정일", "청약일"])
        m_se = re.search(r"(?:구주주[^\d]{0,50})?종료일[^\d]{0,20}(\d{4}[\.\-년]\s*\d{1,2}[월\.\-]\s*\d{1,2})일?", text)
        if m_se:
            sub_end_date = to_standard_date(m_se.group(1))

    # 납입일, 배당기산일, 주식유통일
    pay_date = find_date(["12. 납입일", "주금납입일", "납입일"])
    div_start_date = find_date(["14. 신주의 배당기산일", "신주의 배당기산일", "배당기산일"])
    listing_date = find_date(["16. 신주의 상장예정일", "신주의 상장예정일", "신주상장예정일", "신주의 상장"])

    # 실권주 청약일 (1순위: 표 탐색, 2순위: 본문 정규식)
    forfeit_date = ""
    for tr in soup.find_all("tr"):
        tr_text = tr.get_text()
        if ("일반공모청약" in tr_text or "실권주" in tr_text) and "구주주" not in tr_text:
            dates = re.findall(r"(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})", tr_text)
            if dates:
                forfeit_date = to_standard_date(dates[0])
                break

    if not forfeit_date:
        m_ff = re.search(r"(?:실권주\s*일반공모청약|실권주\s*청약|일반공모청약)[^\d]{0,80}(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})", text)
        if m_ff:
            forfeit_date = to_standard_date(m_ff.group(1))
    if not forfeit_date:
        forfeit_date = find_date(["실권주청약일", "일반공모청약일", "실권주일반공모청약일"])

    return {
        "record_date": record_date,
        "ex_rights_date": ex_rights_date,
        "issue_price": issue_price,
        "applied_ratio": applied_ratio,
        "sub_date": sub_start_date,
        "sub_end_date": sub_end_date,
        "forfeit_date": forfeit_date,
        "div_start_date": div_start_date,
        "pay_date": pay_date,
        "listing_date": listing_date,
        "rights_start": rights_start,
        "rights_end": rights_end,
        "price_fixed_date": find_date(["확정예정일", "확정발행가액공고", "발행가액확정일"]),
    }

# 4. 노션 태스크 등록 함수
def create_notion_task(title: str, event_date: str):
    notion.pages.create(
        parent={"database_id": DATABASE_ID},
        properties={
            "이름": {"title": [{"text": {"content": title}}]},
            "category": {"relation": [{"id": CATEGORY_RELATION_ID}]},
            "구분": {"select": {"name": "유상증자"}},
            "일정": {"date": {"start": event_date}},
            "완료": {"checkbox": False},
            "텍스트 1": {"rich_text": []},
        },
        children=[
            {
                "object": "block",
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": "💡"},
                    "color": "gray_background",
                    "rich_text": [{"type": "text", "text": {"content": "메모 : "}}],
                },
            }
        ],
    )

# 5. UI 화면 렌더링
st.set_page_config(page_title="[국내공시] 유상증자", page_icon="📊", layout="centered")

st.markdown(
    """
    <style>
    .block-container { max-width: 820px
