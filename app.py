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

    corp_name = re.split(r"\(정정\)|유상증자", title)[0].strip()
    if not corp_name:
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
# 3. 공시 본문에서 시스템 입력 파라미터 파싱
# ==========================================
def parse_offering_schedule_from_contents(html_content: str):
  if not html_content:
    return {}

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
        return f"{parts[0]}/{int(parts[1]):02d}/{int(parts[2]):02d}"
    return ""

  record_date = find_date(["신주배정기준일", "배정기준일"])

  ex_rights_date = ""
  if record_date:
    try:
      dt = datetime.strptime(record_date, "%Y/%m/%d")
      dt_prev = dt - timedelta(days=1)
      while dt_prev.weekday() >= 5:  # 5=토, 6=일
        dt_prev -= timedelta(days=1)
      ex_rights_date = dt_prev.strftime("%Y/%m/%d")
    except Exception:
      pass

  issue_price = ""
  price_match = re.search(r"(?:확정발행가액|예정발행가액|발행가액)[^\d]{0,40}([\d,]+)\s*원", text)
  if price_match:
    raw_num = price_match.group(1).replace(",", "")
    try:
      issue_price = f"{int(raw_num):,}"
    except Exception:
      issue_price = raw_num

  applied_ratio = ""
  ratio_match = re.search(r"(?:1주당\s*신주배정주식수|1주당\s*신주배정비율|신주배정비율)[^\d]{0,30}(\d+\.\d+)", text)
  if ratio_match:
    try:
      raw_val = float(ratio_match.group(1))
      applied_ratio = f"{raw_val * 100:.11f}".rstrip("0") + "%"
      if not applied_ratio.endswith("%"):
        applied_ratio += "%"
    except Exception:
      applied_ratio = ratio_match.group(1)

  rights_start = ""
  rights_end = ""
  rights_period_match = re.search(
      r"신주인수권증서\s*상장기간[^\d]{0,20}(\d{4}[년\.\-]\s*\d{1,2}[월\.\-]\s*\d{1,2}일?)\s*~?\s*(\d{4}[년\.\-]\s*\d{1,2}[월\.\-]\s*\d{1,2}일?)?",
      text,
  )
  if rights_period_match:
    def clean_d(raw):
      if not raw:
        return ""
      c = raw.replace("년", "-").replace("월", "-").replace("일", "").replace(".", "-").replace(" ", "")
      p = c.split("-")
      return f"{p[0]}/{int(p[1]):02d}/{int(p[2]):02d}"

    rights_start = clean_d(rights_period_match.group(1))
    rights_end = clean_d(rights_period_match.group(2))

  if not rights_start:
    rights_start = find_date([
        "신주인수권증서 상장기간",
        "신주인수권증서상장기간",
        "신주인수권 상장기간",
        "신주인수권상장기간",
        "신주인수권증서 상장예정일",
        "신주인수권증서상장예정일",
        "신주인수권 상장예정일",
        "신주인수권상장예정일",
        "신주인수권증서 상장일",
        "신주인수권상장일",
    ])

  return {
      "record_date": record_date,
      "ex_rights_date": ex_rights_date,
      "issue_price": issue_price,
      "applied_ratio": applied_ratio,
      "sub_date": find_date(["구주주청약일", "구주주청약", "청약예정일", "청약일"]),
      "sub_end_date": find_date(["청약종료일", "청약종료"]),
      "pay_date": find_date(["주금납입일", "납입일"]),
      "listing_date": find_date(["신주상장예정일", "상장예정일", "주식유통일"]),
      "rights_start": rights_start,
      "rights_end": rights_end,
      "price_fixed_date": find_date(["확정발행가액공고", "발행가액확정일", "확정예정일"]),
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
    page_title="[국내공시] 유상증자",
    page_icon="📊",
    layout="centered"
)

st.markdown(
    """
    <style>
    .block-container {
        max-width: 820px !important;
        padding-top: 1.5rem;
        padding-bottom: 2rem;
    }
    .system-title {
        font-size: 13px;
        color: #ff8c8c;
        font-weight: bold;
        margin-bottom: 12px;
    }
    .notice-item-divider {
        margin-top: 0.6rem;
        margin-bottom: 0.9rem;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# 💡 상단 타이틀 간결화
st.title("📊 [국내공시] 유상증자")

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
# TAB 1: 일정 등록
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
              p = parse_offering_schedule_from_contents(notice.get("contents", ""))
              registered = []

              if p.get("ex_rights_date"):
                d_fmt = p["ex_rights_date"].replace("/", "-")
                create_notion_task(f"[{current_code}] 유상증자 (권리락일)", d_fmt)
                registered.append(f"권리락일: {d_fmt}")

              if p.get("rights_start"):
                d_fmt = p["rights_start"].replace("/", "-")
                create_notion_task(f"[{current_code}] 유상증자 (신주인수권상장)", d_fmt)
                registered.append(f"신주인수권상장: {d_fmt}")

              if p.get("sub_date"):
                d_fmt = p["sub_date"].replace("/", "-")
                create_notion_task(f"[{current_code}] 유상증자 (구주주청약)", d_fmt)
                registered.append(f"청약일: {d_fmt}")

              if registered:
                st.success(f"✅ 일정 등록 완료!\n- " + "\n- ".join(registered))
                st.balloons()
              else:
                st.warning("공시 본문에서 핵심 일정을 찾지 못했습니다.")
            except Exception as e:
              st.error(f"등록 실패: {e}")

      st.markdown('<div class="notice-item-divider"></div>', unsafe_allow_html=True)

  elif st.session_state.get("current_code") and not st.session_state.get("found_notices"):
    st.info("해당 종목의 최근 공시 중 유상증자 관련 공시가 없습니다.")
  else:
    st.info("상단에 종목코드(6자리)를 입력하고 [🔍 공시 조회]를 눌러주세요.")


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
    data = parse_offering_schedule_from_contents(selected_notice.get("contents", ""))

    inst_code_5 = current_code[:5] if len(current_code) >= 5 else current_code
    corp_name = selected_notice.get("corp_name", "")

    st.write("")
    
    # [상단 박스] - 기본 정보 & 일정
    st.markdown('<div class="system-title">■ 기본 정보 및 청약/배정 일정</div>', unsafe_allow_html=True)
    with st.container(border=True):
      r1_1, r1_2, r1_3 = st.columns([1.5, 3.2, 1.3])
      with r1_1:
        st.text_input("발행기관", value=inst_code_5, disabled=True)
      with r1_2:
        st.text_input("발행회사명", value=corp_name, disabled=True)
      with r1_3:
        st.text_input("증자방법", value="유상", disabled=True)

      r2_1, r2_2, r2_3 = st.columns([1.5, 3.2, 1.3])
      with r2_1:
        st.text_input("배정방법", value="11", disabled=True)
      with r2_2:
        st.text_input("배정형태", value="보->보", disabled=True)
      with r2_3:
        st.write("")

      st.markdown("---")

      r3_1, r3_2 = st.columns(2)
      with r3_1:
        st.text_input("권리락일", value=data.get("ex_rights_date", ""))
        st.text_input("발행가", value=data.get("issue_price", ""))
      with r3_2:
        st.text_input("배정기준일", value=data.get("record_date", ""))
        st.text_input("적용비율", value=data.get("applied_ratio", ""))

      st.markdown("---")

      r4_1, r4_2 = st.columns(2)
      with r4_1:
        st.text_input("청약일", value=data.get("sub_date", ""))
        st.text_input("주금납입일", value=data.get("pay_date", ""))
        st.text_input("주식유통일 (신주상장)", value=data.get("listing_date", ""))
      with r4_2:
        st.text_input("실권주청약일", value=data.get("sub_end_date", ""))
        st.text_input("배당기산일", value=data.get("record_date", ""))
        st.selectbox("공시확정", ["여", "부"], index=0)

    # [하단 박스] - 신주인수권증서 관련 사항
    st.write("")
    st.markdown('<div class="system-title">■ 신주인수권증서 등록 정보</div>', unsafe_allow_html=True)
    with st.container(border=True):
      st.radio("신주인수권증서발행", ["여", "부"], index=0, horizontal=True)

      s1_1, s1_2 = st.columns(2)
      with s1_1:
        st.text_input("신주인수권증서상장일", value=data.get("rights_start", ""))
        st.text_input("발행가확정일", value=data.get("price_fixed_date", ""))
        st.text_input("유상청약일 (시작)", value=data.get("sub_date", ""))
      with s1_2:
        st.text_input("신주인수권증서폐지일", value=data.get("rights_end", ""))
        st.text_input("공시기준확정일", value=data.get("record_date", ""))
        st.text_input("유상청약일 (종료)", value=data.get("sub_end_date", ""))

      st.text_input("발행비율", value="100.0000000000 (%)")

  else:
    st.info("상단에 종목코드(6자리)를 입력하고 [🔍 공시 조회]를 눌러주시면 시스템 입력 데이터가 자동 채워집니다.")
