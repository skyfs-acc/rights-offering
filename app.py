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
# 2. 최근 30건 중 '유상증자' 공시 수신 & 제목 정제
# ==========================================
def fetch_naver_notices(stock_code: str):
  """네이버 증권 API로부터 최근 30건 중 유상증자 공시만 추출"""
  url = f"https://stock.naver.com/api/domestic/detail/notice?itemCode={stock_code}&startIdx=0&pageSize=30"
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

    # 유상증자 공시만 통과
    if "유상증자" not in title:
      continue

    notice_no = item.get("no", "")
    raw_datetime = item.get("datetime", "")
    html_contents = item.get("contents", "")

    # 날짜 포맷팅 (YYYY.MM.DD)
    date_str = ""
    if raw_datetime:
      date_part = raw_datetime.split("T")[0]
      date_str = date_part.replace("-", ".")

    filtered_list.append({
        "title": title,
        "no": str(notice_no),
        "date": date_str,
        "contents": html_contents,
    })

  return filtered_list


# ==========================================
# 3. 공시 본문(contents)에서 3대 핵심 일정 파싱
# ==========================================
def parse_offering_schedule_from_contents(html_content: str):
  """contents 내부 HTML에서 신주배정기준일, 청약일, 납입일 추출"""
  if not html_content:
    return {"record_date": None, "sub_start": None, "pay_date": None}

  soup = BeautifulSoup(html_content, "html.parser")
  text = soup.get_text()

  def find_date(keywords):
    for kw in keywords:
      pattern = rf"{kw}[^\d]{{0,50}}(\d{{4}}[\.\-년]\s*\d{{1,2}}[\.\-월]\s*\d{{1,2}})"
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
          ["구주주청약일", "구주주청약", "청약예정일", "청약일", "청약기간"]
      ),
      "pay_date": find_date(["주금납입일", "납입일"]),
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
    page_title="[국내공시] 유상증자 등록",
    page_icon="📊",
    layout="centered"
)

# 최대 제목 길이에 따라 컨테이너 폭을 동적으로 맞추는 CSS
max_len = 0
if st.session_state.get("found_notices"):
  titles = [n.get("title", "") for n in st.session_state["found_notices"]]
  if titles:
    max_len = max(len(t) for t in titles)

# 기본 750px ~ 가장 긴 제목에 맞춰 최대 1050px까지 유동적으로 확장
calc_width = min(max(750, max_len * 18 + 180), 1050)

st.markdown(
    f"""
    <style>
    .block-container {{
        max-width: {calc_width}px !important;
        padding-top: 2rem;
        padding-bottom: 2rem;
    }}
    .notice-item {{
        padding: 8px 0;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📊 [국내공시] 유상증자 등록")
st.caption("종목코드를 입력하면 최근 유상증자 공시 일정을 찾아 노션에 등록합니다.")

# 종목코드 입력창
col_in1, col_in2 = st.columns([4, 1.2])
with col_in1:
  stock_code = st.text_input(
      "종목코드 입력",
      placeholder="예: 448730",
      label_visibility="collapsed"
  ).strip()
with col_in2:
  search_btn = st.button("🔍 공시 조회", type="primary", use_container_width=True)

if search_btn:
  if not stock_code:
    st.warning("종목코드를 입력해 주세요.")
  else:
    with st.spinner(f"종목코드 [{stock_code}] 유상증자 공시 확인 중..."):
      try:
        items = fetch_naver_notices(stock_code)
        st.session_state["found_notices"] = items
        st.session_state["current_code"] = stock_code
        st.rerun()
      except Exception as e:
        st.error(f"공시 목록 조회 실패: {e}")

if st.session_state.get("found_notices"):
  notices = st.session_state["found_notices"]
  current_code = st.session_state.get("current_code", "")

  st.write("")
  st.markdown(f"**📋 발견된 유상증자 공시 {len(notices)}건**")

  for idx, notice in enumerate(notices):
    col_a, col_b = st.columns([5, 1.2])
    with col_a:
      # 💡 1행: 공시 제목
      st.markdown(f"**{notice.get('title', '')}**")
      # 💡 2행: 날짜는 무조건 다음 줄에 배치
      date_val = notice.get("date", "")
      date_display = f"📅 공시일자: `{date_val}`" if date_val else "📅 공시일자: -"
      st.caption(date_display)
    with col_b:
      st.write("")  # 수직 위치 밸런스 조정
      if st.button("🚀 일정등록", key=f"btn_{idx}", use_container_width=True):
        with st.spinner("일정 분석 및 등록 중..."):
          try:
            sched = parse_offering_schedule_from_contents(notice.get("contents", ""))
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
                  "공시 본문에서 핵심 일정을 찾지 못했습니다. 본문 세부 내용을 확인해 주세요."
              )
          except Exception as e:
            st.error(f"등록 실패: {e}")
elif st.session_state.get("current_code") and not st.session_state.get(
    "found_notices"
):
  st.info("해당 종목의 최근 공시 중 유상증자 관련 공시가 없습니다.")
