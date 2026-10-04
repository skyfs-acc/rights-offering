import re
from urllib.parse import parse_qs, urlparse
from bs4 import BeautifulSoup
from notion_client import Client
import requests
import streamlit as st

# ==========================================
# 1. 노션 설정
# ==========================================
NOTION_TOKEN = st.secrets["NOTION_TOKEN"]
DATABASE_ID = st.secrets["DATABASE_ID"]

notion = Client(auth=NOTION_TOKEN)


# ==========================================
# 2. KIND 공시 본문 파싱 함수 (403 우회 및 iframe 대응)
# ==========================================
def parse_kind_disclosure(url: str):
  # URL 파싱 (acptno 추출)
  parsed_url = urlparse(url)
  qs = parse_qs(parsed_url.query)
  acptno = qs.get("acptno", [""])[0]

  session = requests.Session()
  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
          " like Gecko) Chrome/128.0.0.0 Safari/537.36"
      ),
      "Accept": (
          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
      ),
      "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
      "Referer": "https://kind.krx.co.kr/",
      "Connection": "keep-alive",
  }

  # 1차 메인 뷰어 접속
  res = session.get(url, headers=headers, timeout=10)
  res.raise_for_status()
  soup = BeautifulSoup(res.text, "html.parser")

  # 종목명 추출 (메인 타이틀)
  corp_name = "종목명미상"
  title_elem = soup.find("title")
  if title_elem and "-" in title_elem.text:
    corp_name = title_elem.text.split("-")[0].strip()

  # KIND 공시 본문은 내부 iframe(searchChildMain.do 등)에 담겨 있는 경우가 많음
  iframe = soup.find("iframe", id="mainDoc") or soup.find("iframe")
  target_text = soup.get_text()

  if iframe and iframe.get("src"):
    src = iframe.get("src")
    if not src.startswith("http"):
      src = "https://kind.krx.co.kr" + src
    headers["Referer"] = url
    res_child = session.get(src, headers=headers, timeout=10)
    if res_child.status_code == 200:
      soup_child = BeautifulSoup(res_child.text, "html.parser")
      target_text = soup_child.get_text()
  elif acptno:
    # 뷰어 직접 조회 엔드포인트 보조 요청
    sub_url = f"https://kind.krx.co.kr/common/searchChildMain.do?method=searchChildMain&acptno={acptno}"
    try:
      headers["Referer"] = url
      res_sub = session.get(sub_url, headers=headers, timeout=10)
      if res_sub.status_code == 200:
        soup_sub = BeautifulSoup(res_sub.text, "html.parser")
        target_text += " " + soup_sub.get_text()
    except Exception:
      pass

  # 날짜 추출 헬퍼
  def find_date(keyword):
    pattern = rf"{keyword}[^\d]{{0,35}}(\d{{4}}[\.\-년]\s*\d{{1,2}}[\.\-월]\s*\d{{1,2}})"
    match = re.search(pattern, target_text)
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
      "sub_start": find_date("청약예정일")
      or find_date("청약기간")
      or find_date("구주주청약"),
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
          "category": {"select": {"name": "📊 유상증자 / 무상증자"}},
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
    page_title="[국내공시] 유상증자 등록", page_icon="📊", layout="centered"
)
st.title("📊 [국내공시] 유상증자 등록")
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
        data = parse_kind_disclosure(disclosure_url.strip())
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
              "공시 본문에서 유상증자 일정(신주배정기준일/청약일/납입일)을 찾지"
              " 못했습니다. URL을 확인해 주세요."
          )
      except Exception as e:
        st.error(f"오류가 발생했습니다: {e}")
