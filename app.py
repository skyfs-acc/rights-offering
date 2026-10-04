import re
import requests
from bs4 import BeautifulSoup
from notion_client import Client
import streamlit as st

# ==========================================
# 1. 노션 설정 (Streamlit Secrets에서 안전하게 로드)
# ==========================================
NOTION_TOKEN = st.secrets["NOTION_TOKEN"]
DATABASE_ID = st.secrets["DATABASE_ID"]

notion = Client(auth=NOTION_TOKEN)

# ==========================================
# 2. KIND 공시 본문 파싱 함수
# ==========================================
def parse_kind_disclosure(url: str):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
            " like Gecko) Chrome/115.0.0.0 Safari/537.36"
        )
    }
    res = requests.get(url, headers=headers)
    res.raise_for_status()

    soup = BeautifulSoup(res.text, "html.parser")
    text = soup.get_text()

    # 종목명 추출
    corp_name = "종목명미상"
    title_elem = soup.find("title")
    if title_elem and "-" in title_elem.text:
        corp_name = title_elem.text.split("-")[0].strip()

    # 날짜 추출 정규식
    def find_date(keyword):
        pattern = rf"{keyword}[^\d]{{0,30}}(\d{{4}}[\.\-년]\s*\d{{1,2}}[\.\-월]\s*\d{{1,2}})"
        match = re.search(pattern, text)
        if match:
            raw = match.group(1)
            cleaned = (
                raw.replace("년", "-")
                .replace("월", "-")
                .replace("일", "")
                .replace(".", "-")
                .replace(" ", "")
            )
            parts = cleaned.split("-")
            return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        return None

    return {
        "corp_name": corp_name,
        "record_date": find_date("신주배정기준일"),
        "sub_start": find_date("청약예정일") or find_date("청약기간"),
        "pay_date": find_date("납입일"),
    }

# ==========================================
# 3. 노션 데이터베이스 카드 생성 함수
# ==========================================
def create_notion_task(title: str, event_date: str, note: str, url: str):
    notion.pages.create(
        parent={"database_id": DATABASE_ID},
        properties={
            "이름": {"title": [{"text": {"content": title}}]},
            "category": {"select": {"name": "📢 국내공시"}},
            "구분": {"select": {"name": "공시"}},
            "일정": {"date": {"start": event_date}},
            "완료": {"checkbox": False},
            "텍스트 1": {"rich_text": [{"text": {"content": note}}]},
        },
    )

# ==========================================
# 4. Streamlit UI 구성
# ==========================================
st.set_page_config(
    page_title="유상증자 공시 일정 등록", page_icon="📢", layout="centered"
)
st.title("📢 유상증자 일정 원클릭 노션 등록")
st.write(
    "KIND 공시 상세 페이지 URL을 입력하면 핵심 일정이 노션 TO DO LIST에 자동"
    " 등록됩니다."
)

disclosure_url = st.text_input(
    "KIND 공시 상세 페이지 URL",
    placeholder="https://kind.krx.co.kr/common/disclsviewer.do?...",
)

if st.button("🚀 노션 TO DO LIST에 등록하기", type="primary"):
    if not disclosure_url:
        st.warning("공시 URL을 입력해주세요.")
    else:
        with st.spinner("공시 내용을 분석하고 노션에 등록하는 중입니다..."):
            try:
                data = parse_kind_disclosure(disclosure_url)
                corp = data["corp_name"]
                registered = []

                if data["record_date"]:
                    create_notion_task(
                        f"[{corp}] 유상증자 신주배정기준일",
                        data["record_date"],
                        "권리락/배정 기준일 확인",
                        disclosure_url,
                    )
                    registered.append(f"신주배정기준일: {data['record_date']}")

                if data["sub_start"]:
                    create_notion_task(
                        f"[{corp}] 유상증자 청약 개시",
                        data["sub_start"],
                        "청약 신청 진행 및 자금 확인",
                        disclosure_url,
                    )
                    registered.append(f"청약개시일: {data['sub_start']}")

                if data["pay_date"]:
                    create_notion_task(
                        f"[{corp}] 유상증자 주금 납입일",
                        data["pay_date"],
                        "주금 납입 및 회계처리 확인",
                        disclosure_url,
                    )
                    registered.append(f"납입일: {data['pay_date']}")

                if registered:
                    st.success(
                        f"✅ [{corp}] 일정 등록 완료!\n- " + "\n- ".join(registered)
                    )
                else:
                    st.error(
                        "공시 본문에서 날짜 정보를 찾지 못했습니다. URL을 확인해 주세요."
                    )
            except Exception as e:
                st.error(f"오류가 발생했습니다: {e}")
