import io
import re
from bs4 import BeautifulSoup
from notion_client import Client
import requests
import streamlit as st

# ==========================================
# 1. 노션 설정 로드 (Streamlit Secrets)
# ==========================================
NOTION_TOKEN = st.secrets["NOTION_TOKEN"]
DATABASE_ID = st.secrets["DATABASE_ID"]

notion = Client(auth=NOTION_TOKEN)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Referer": "https://stock.naver.com/",
}


# ==========================================
# 2. 최근 30건 전체 공시 목록 조회
# ==========================================
def fetch_naver_notices(stock_code: str):
  """종목코드(6자리) 기준 최근 30건 전체 공시 조회"""
  url = f"https://stock.naver.com/api/domestic/detail/notice?itemCode={stock_code}&startIdx=0&pageSize=30"
  res = requests.get(url, headers=HEADERS, timeout=10)
  res.raise_for_status()
  data = res.json()

  notices = []
  if isinstance(data, list):
    notices = data
  elif isinstance(data, dict):
    notices = data.get("notices") or data.get("list") or data.get("result") or []

  result_list = []
  for item in notices:
    title = item.get("title", "")
    notice_id = (
        item.get("noticeId")
        or item.get("articleId")
        or item.get("id")
        or item.get("rcpNo")
    )

    notice_date = (
        item.get("submitDate")
        or item.get("dt")
        or item.get("rceptDt")
        or item.get("date")
        or ""
    )
    if len(notice_date) == 8 and notice_date.isdigit():
      notice_date = f"{notice_date[:4]}.{notice_date[4:6]}.{notice_date[6:]}"

    result_list.append({
        "title": title,
        "notice_id": str(notice_id),
        "date": notice_date,
        "is_offering": "유상증자" in title,
    })

  return result_list


# ==========================================
# 3. 공시 본문 수신 및 핵심 일정 파싱
# ==========================================
def parse_offering_schedule(stock_code: str, notice_id: str):
  detail_url = (
      f"https://stock.naver.com/domestic/stock/{stock_code}/notice/{notice_id}"
  )
  res = requests.get(detail_url, headers=HEADERS, timeout=10)
  res.raise_for_status()

  soup = BeautifulSoup(res.text, "html.parser")
  text = soup.get_text()

  def find_date(keywords):
    for kw in keywords:
      pattern = rf"{kw}[^\d]{{0,35}}(\d{{4}}[\.\-년]\s*\d{{1,2}}[\.\-월]\s*\d{{1,2}})"
      m = re.search(pattern, text)
      if m:
        raw = m.group(1)
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
      "record_date": find_date(["신주배정기준일", "배정기준일"]),
      "sub_start": find_date(
          ["청약예정일", "구주주청약", "청약기간", "청약일"]
      ),
      "pay_date": find_date(["납입일", "주금납입일"]),
  }


# ==========================================
# 4. 노션 데이터베이스 등록 함수
# ==========================================
def create_notion_task(title: str, event_date: str, note: str):
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
# 5. Streamlit 메인 UI
# ==========================================
st.set_page_config(
    page_title="[국내공시] 유상증자 등록", page_icon="📊", layout="centered"
)
st.title("📊 [국내공시] 유상증자 등록")
st.write(
    "종목코드(6자리)를 입력하면 최근 30건의 공시 목록을 불러와 노션에 일정을"
    " 등록합니다."
)

col1, col2 = st.columns([3, 1])
with col1:
  stock_code = st.text_input(
      "종목코드 입력 (예: 448730)",
      placeholder="6자리 종목코드 입력",
  ).strip()
with col2:
  st.write("")
  st.write("")
  search_btn = st.button("🔍 공시 조회", type="primary")

if search_btn:
  if not stock_code:
    st.warning("종목코드를 입력해 주세요.")
  else:
    with st.spinner(f"종목코드 [{stock_code}] 최근 30건 공시 조회 중..."):
      try:
        items = fetch_naver_notices(stock_code)
        st.session_state["found_notices"] = items
        st.session_state["current_code"] = stock_code
      except Exception as e:
        st.error(f"공시 목록 조회 실패: {e}")

if st.session_state.get("found_notices"):
  notices = st.session_state["found_notices"]
  current_code = st.session_state.get("current_code", "")

  st.write(f"📋 **최근 30건 공시 목록:**")

  for idx, notice in enumerate(notices):
    col_a, col_b = st.columns([3.5, 1.2])
    with col_a:
      date_str = f" ({notice.get('date', '')})" if notice.get("date") else ""
      is_offering = notice.get("is_offering", "유상증자" in notice.get("title", ""))
      prefix = "📌 " if is_offering else ""
      st.markdown(f"{prefix}**{notice.get('title', '')}**{date_str}")
    with col_b:
      if st.button("🚀 일정등록", key=f"btn_{idx}"):
        with st.spinner("본문 일정 분석 및 노션 등록 중..."):
          try:
            sched = parse_offering_schedule(current_code, notice.get("notice_id", ""))
            registered = []

            if sched["record_date"]:
              create_notion_task(
                  f"[{current_code}] 신주배정기준일",
                  sched["record_date"],
                  f"신주배정기준일 ({notice.get('title', '')})",
              )
              registered.append(f"기준일: {sched['record_date']}")

            if sched["sub_start"]:
              create_notion_task(
                  f"[{current_code}] 청약 개시",
                  sched["sub_start"],
                  f"청약개시일 ({notice.get('title', '')})",
              )
              registered.append(f"청약일: {sched['sub_start']}")

            if sched["pay_date"]:
              create_notion_task(
                  f"[{current_code}] 주금 납입일",
                  sched["pay_date"],
                  f"주금납입일 ({notice.get('title', '')})",
              )
              registered.append(f"납입일: {sched['pay_date']}")

            if registered:
              st.success(
                  f"✅ 일정 등록 완료!\n- " + "\n- ".join(registered)
              )
              st.balloons()
            else:
              st.warning(
                  "공시 본문에서 핵심 일정을 찾지 못했습니다. 유상증자"
                  " 본문인지 확인해 주세요."
              )
          except Exception as e:
            st.error(f"등록 실패: {e}")
elif st.session_state.get("current_code") and not st.session_state.get(
    "found_notices"
):
  st.info("해당 종목의 최근 공시 목록이 비어 있습니다.")
