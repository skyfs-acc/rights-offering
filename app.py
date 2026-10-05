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

CATEGORY_RELATION_ID = "ab0b581c7f5d8326a7f2812fe3dd5fa6"

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

    if "유상증자" not in title:
      continue

    notice_no = item.get("no", "")
    raw_datetime = item.get("datetime", "")
    html_contents = item.get("contents", "")

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
# 3. 공시 본문에서 핵심 일정 파싱
# ==========================================
def parse_offering_schedule_from_contents(html_content: str):
  """본문 HTML에서 신주배정기준일, 청약일, 신주인수권상장일 등 추출"""
  if not html_content:
    return {
        "record_date": None,
        "sub_start": None,
        "sub_end": None,
        "rights_listing_date": None,
        "pay_date": None,
        "listing_date": None,
        "issue_price": None,
        "ratio": None,
    }

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

  # 1주당 예정/확정 발행가액
  issue_price = None
  price_match = re.search(r"(?:확정발행가액|예정발행가액|발행가액)[^\d]{0,40}([\d,]+)\s*원", text)
  if price_match:
    issue_price = price_match.group(1).replace(",", "")

  # 1주당 신주배정비율
  ratio = None
  ratio_match = re.search(r"(?:1주당\s*신주배정비율|신주배정비율)[^\d]{0,30}(\d+\.\d+)", text)
  if ratio_match:
    ratio = ratio_match.group(1)

  return {
      "record_date": find_date(["신주배정기준일", "배정기준일"]),
      "sub_start": find_date(["구주주청약일", "구주주청약", "청약예정일", "청약일"]),
      "sub_end": find_date(["청약종료일", "청약종료"]),
      # 💡 신주인수권증서 상장일 / 거래개시일
      "rights_listing_date": find_date([
          "신주인수권증서상장예정일",
          "신주인수권증서 상장예정일",
          "신주인수권상장예정일",
          "신주인수권 상장예정일",
          "신주인수권증서 상장일",
          "신주인수권 상장일",
          "신주인수권증서 매매기간",
          "신주인수권증서매매기간",
      ]),
      "pay_date": find_date(["주금납입일", "납입일"]),
      "listing_date": find_date(["신주상장예정일", "상장예정일", "신주의상장"]),
      "issue_price": issue_price,
      "ratio": ratio,
  }


# ==========================================
# 4. 노션 데이터베이스 등록 함수
# ==========================================
def create_notion_task(title: str, event_date: str):
  notion.pages.create(
      parent={"database_id": DATABASE_ID},
      properties={
          "이름": {"title": [{"text": {"content": title}}]},
          "category": {
              "relation": [{"id": CATEGORY_RELATION_ID}]
          },
          "구분": {"select": {"name": "유상증자"}},
          "일정": {"date": {"start": event_date}},
          "완료": {"checkbox": False},
          "텍스트 1": {"rich_text": []},
      },
  )


# ==========================================
# 5. Streamlit 메인 설정 & 스타일
# ==========================================
st.set_page_config(
    page_title="[국내공시] 유상증자 관리",
    page_icon="📊",
    layout="centered"
)

max_len = 0
if st.session_state.get("found_notices"):
  titles = [n.get("title", "") for n in st.session_state["found_notices"]]
  if titles:
    max_len = max(len(t) for t in titles)

calc_width = min(max(750, max_len * 18 + 180), 1050)

st.markdown(
    f"""
    <style>
    .block-container {{
        max-width: {calc_width}px !important;
        padding-top: 2rem;
        padding-bottom: 2rem;
    }}
    .notice-item-divider {{
        margin-top: 0.6rem;
        margin-bottom: 0.9rem;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📊 [국내공시] 유상증자 관리")

# ==========================================
# 6. 상단 공통 종목코드 검색창
# ==========================================
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
        st.session_state["selected_notice_idx"] = 0
        st.rerun()
      except Exception as e:
        st.error(f"공시 목록 조회 실패: {e}")

# ==========================================
# 7. 2대 탭 구성 (일정 등록 / 시스템 입력)
# ==========================================
tab_schedule, tab_system = st.tabs(["📅 일정 등록", "💻 시스템 입력"])

# ------------------------------------------
# TAB 1: 일정 등록 (납입일 제외 -> 신주인수권상장 반영)
# ------------------------------------------
with tab_schedule:
  if st.session_state.get("found_notices"):
    notices = st.session_state["found_notices"]
    current_code = st.session_state.get("current_code", "")

    st.write("")
    st.markdown(f"**📋 발견된 유상증자 공시 {len(notices)}건**")

    for idx, notice in enumerate(notices):
      col_a, col_b = st.columns([5, 1.2])
      with col_a:
        date_val = notice.get("date", "")
        date_display = f"📅 `{date_val}`" if date_val else "📅 `일자 미정`"
        st.caption(date_display)
        st.markdown(f"**{notice.get('title', '')}**")
        
      with col_b:
        st.write("")
        if st.button("🚀 일정등록", key=f"btn_sched_{idx}", use_container_width=True):
          with st.spinner("일정 분석 및 등록 중..."):
            try:
              sched = parse_offering_schedule_from_contents(notice.get("contents", ""))
              registered = []

              # 1. 신주배정기준일
              if sched["record_date"]:
                create_notion_task(
                    f"[{current_code}] 유상증자 (신주배정기준일)",
                    sched["record_date"],
                )
                registered.append(f"기준일: {sched['record_date']}")

              # 2. 신주인수권 상장일
              if sched["rights_listing_date"]:
                create_notion_task(
                    f"[{current_code}] 유상증자 (신주인수권상장)",
                    sched["rights_listing_date"],
                )
                registered.append(f"신주인수권상장: {sched['rights_listing_date']}")

              # 3. 구주주청약 개시일
              if sched["sub_start"]:
                create_notion_task(
                    f"[{current_code}] 유상증자 (구주주청약)",
                    sched["sub_start"],
                )
                registered.append(f"청약일: {sched['sub_start']}")

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

      st.markdown('<div class="notice-item-divider"></div>', unsafe_allow_html=True)

  elif st.session_state.get("current_code") and not st.session_state.get(
      "found_notices"
  ):
    st.info("해당 종목의 최근 공시 중 유상증자 관련 공시가 없습니다.")
  else:
    st.info("상단에 종목코드를 입력하고 [🔍 공시 조회]를 눌러주세요.")


# ------------------------------------------
# TAB 2: 시스템 입력 UI
# ------------------------------------------
with tab_system:
  if st.session_state.get("found_notices"):
    notices = st.session_state["found_notices"]
    current_code = st.session_state.get("current_code", "")

    options = [f"[{n.get('date', '-')}] {n.get('title', '')}" for n in notices]
    selected_idx = st.selectbox(
        "기준 공시 선택",
        range(len(options)),
        format_func=lambda x: options[x],
        key="system_notice_select"
    )

    selected_notice = notices[selected_idx]
    parsed_info = parse_offering_schedule_from_contents(selected_notice.get("contents", ""))

    st.markdown("#### 📝 시스템 등록 항목 가이드")
    st.caption("공시 본문에서 자동 추출된 값입니다. 확인 후 수정하거나 바로 복사하여 시스템에 입력하세요.")

    st.markdown("##### 1. 주요 일정")
    col_d1, col_d2 = st.columns(2)
    with col_d1:
      val_record = st.text_input("신주배정기준일", value=parsed_info.get("record_date") or "", key="sys_record")
      val_rights = st.text_input("신주인수권 상장일", value=parsed_info.get("rights_listing_date") or "", key="sys_rights")
      val_sub_start = st.text_input("구주주청약 시작일", value=parsed_info.get("sub_start") or "", key="sys_sub_start")
    with col_d2:
      val_sub_end = st.text_input("구주주청약 종료일", value=parsed_info.get("sub_end") or "", key="sys_sub_end")
      val_pay = st.text_input("주금납입일", value=parsed_info.get("pay_date") or "", key="sys_pay")
      val_listing = st.text_input("신주상장예정일", value=parsed_info.get("listing_date") or "", key="sys_listing")

    st.markdown("##### 2. 가격 및 배정비율")
    col_p1, col_p2 = st.columns(2)
    with col_p1:
      val_price = st.text_input("발행가액 (원)", value=parsed_info.get("issue_price") or "", key="sys_price")
    with col_p2:
      val_ratio = st.text_input("1주당 신주배정비율", value=parsed_info.get("ratio") or "", key="sys_ratio")

    st.markdown("##### 3. 복사용 텍스트 요약")
    summary_text = (
        f"[종목코드] {current_code}\n"
        f"[기준일] {val_record}\n"
        f"[신주인수권상장] {val_rights}\n"
        f"[청약기간] {val_sub_start} ~ {val_sub_end}\n"
        f"[납입일] {val_pay}\n"
        f"[신주상장예정일] {val_listing}\n"
        f"[발행가액] {val_price}원\n"
        f"[배정비율] {val_ratio}"
    )
    st.code(summary_text, language="text")

  else:
    st.info("상단에 종목코드를 입력하고 [🔍 공시 조회]를 눌러주시면 파라미터가 자동으로 채워집니다.")
