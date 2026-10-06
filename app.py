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

# 회사 워크스페이스용 카테고리 ID (📌 펀드 관리 & 이슈)
CATEGORY_RELATION_ID = "e409fb1b1f8982df81928157b6bd5374"

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
    if len(p) >= 3 and p[0].isdigit() and p[1].isdigit() and p[2].isdigit():
      return f"{p[0]}/{int(p[1]):02d}/{int(p[2]):02d}"
    return ""

  def find_date(keywords):
    for kw in keywords:
      pattern = rf"{kw}[^\d]{{0,50}}(\d{{4}}[\.\-년]\s*\d{{1,2}}[\.\-월]\s*\d{{1,2}})"
      m = re.search(pattern, text)
      if m:
        return to_standard_date(m.group(1))
    return ""

  # ------------------------------------------
  # 1. 신주배정기준일
  # ------------------------------------------
  record_date = find_date(["신주배정기준일", "배정기준일"])

  # ------------------------------------------
  # 2. 권리락일 (기준일 직전 1영업일)
  # ------------------------------------------
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

  # ------------------------------------------
  # 3. 신주 발행가액 추출
  # ------------------------------------------
  issue_price = ""
  m_confirm = re.search(
      r"확정발행가[^\d]{0,40}보통주식[^\d]{0,30}([\d,]+)\s*(?:원)?", text
  )
  if m_confirm:
    val = m_confirm.group(1).replace(",", "")
    if val.isdigit() and int(val) > 0:
      issue_price = f"{int(val):,}"

  if not issue_price:
    m_expect = re.search(
        r"예정발행가[^\d]{0,40}보통주식[^\d]{0,30}([\d,]+)\s*(?:원)?", text
    )
    if m_expect:
      val = m_expect.group(1).replace(",", "")
      if val.isdigit() and int(val) > 0:
        issue_price = f"{int(val):,}"

  if not issue_price:
    m_fallback = re.search(
        r"(?:신주발행가액|발행가액)[^\d]{0,40}([\d,]+)\s*원", text
    )
    if m_fallback:
      val = m_fallback.group(1).replace(",", "")
      if val.isdigit() and int(val) > 0:
        issue_price = f"{int(val):,}"

  # ------------------------------------------
  # 4. 1주당 신주배정주식수
  # ------------------------------------------
  applied_ratio = ""
  ratio_match = re.search(
      r"(?:1주당\s*신주배정주식수|1주당\s*신주배정비율|신주배정비율)[^\d]{0,30}(\d+\.\d+)",
      text,
  )
  if ratio_match:
    try:
      raw_val = float(ratio_match.group(1))
      applied_ratio = f"{raw_val * 100:.11f}".rstrip("0") + "%"
      if not applied_ratio.endswith("%"):
        applied_ratio += "%"
    except Exception:
      applied_ratio = ratio_match.group(1)

  # ------------------------------------------
  # 5. 신주인수권 상장기간 (시작일 ~ 종료일)
  # ------------------------------------------
  rights_start = ""
  rights_end = ""
  rights_period_match = re.search(
      r"신주인수권증서\s*상장기간[^\d]{0,20}(\d{4}[년\.\-]\s*\d{1,2}[월\.\-]\s*\d{1,2}일?)\s*~?\s*(\d{4}[년\.\-]\s*\d{1,2}[월\.\-]\s*\d{1,2}일?)?",
      text,
  )
  if rights_period_match:
    rights_start = to_standard_date(rights_period_match.group(1))
    rights_end = to_standard_date(rights_period_match.group(2))

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

  # ------------------------------------------
  # 6. 구주주 청약일정
  # ------------------------------------------
  sub_start_date = ""
  sub_end_date = ""

  # 표(table) 내부에서 구주주 청약일 행 우선 탐색
  for tr in soup.find_all("tr"):
    tr_text = tr.get_text()
    if "구주주" in tr_text and not sub_start_date:
      dates = re.findall(r"(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})", tr_text)
      if dates:
        sub_start_date = to_standard_date(dates[0])
        if len(dates) > 1:
          sub_end_date = to_standard_date(dates[1])

  if not sub_start_date:
    m_sub_pair = re.search(
        r"구주주[^\d]{0,40}시작일[^\d]{0,20}(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})일?[^\d]{0,40}종료일[^\d]{0,20}(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})일?",
        text,
    )
    if m_sub_pair:
      sub_start_date = to_standard_date(m_sub_pair.group(1))
      sub_end_date = to_standard_date(m_sub_pair.group(2))
    else:
      sub_start_date = find_date(["구주주청약일", "구주주청약", "청약예정일", "청약일"])
      m_sub_end = re.search(
          r"(?:구주주[^\d]{0,50})?종료일[^\d]{0,20}(\d{4}[\.\-년]\s*\d{1,2}[월\.\-]\s*\d{1,2})일?",
          text,
      )
      if m_sub_end:
        sub_end_date = to_standard_date(m_sub_end.group(1))

  # ------------------------------------------
  # 7. 12. 납입일
  # ------------------------------------------
  pay_date = find_date(["12. 납입일", "12.납입일", "주금납입일", "납입일"])

  # ------------------------------------------
  # 8. 14. 신주의 배당기산일
  # ------------------------------------------
  div_start_date = find_date([
      "14. 신주의 배당기산일",
      "14.신주의 배당기산일",
      "신주의 배당기산일",
      "배당기산일",
  ])

  # ------------------------------------------
  # 9. 16. 신주의 상장예정일 (주식유통일)
  # ------------------------------------------
  listing_date = find_date([
      "16. 신주의 상장예정일",
      "16.신주의 상장예정일",
      "신주의 상장예정일",
      "신주의상장예정일",
      "신주상장예정일",
      "신주의 상장",
  ])

  # ------------------------------------------
  # 10. 실권주 청약일 (일반공모청약일) - 1순위: 표 탐색, 2순위: 텍스트 탐색
  # ------------------------------------------
  forfeit_date = ""

  # [1순위] 청약사무취급처 및 청약기간 표(table) 내부 tr 행별 우선 매칭
  for tr in soup.find_all("tr"):
    tr_text = tr.get_text()
    # Case 1: "일반공모청약", Case 2: "실권주 일반공모청약" 또는 "실권주일반공모청약"
    if ("일반공모청약" in tr_text or "실권주" in tr_text) and "구주주" not in tr_text:
      # 해당 tr에서 날짜 패턴 검색
      dates = re.findall(r"(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})", tr_text)
      if dates:
        forfeit_date = to_standard_date(dates[0])
        break

  # [2순위] 표에서 못 찾았을 경우 텍스트 기반 정규식 폴백
  if not forfeit_date:
    # 2026년 06월 23일~ 2026년 06월 24일 또는 양일간 패턴
    m_forfeit_range = re.search(
        r"(?:실권주\s*일반공모청약|실권주\s*청약|일반공모청약)[^\d]{0,80}(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})일?\s*(?:~|-|과|와|,|부터)",
        text,
    )
    if m_forfeit_range:
      forfeit_date = to_standard_date(m_forfeit_range.group(1))

  if not forfeit_date:
    m_forfeit_a = re.search(
        r"실권주[^\d]{0,80}(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})일?\s*(?:과|와|,)\s*(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})일?\s*양일간",
        text,
    )
    if m_forfeit_a:
      forfeit_date = to_standard_date(m_forfeit_a.group(1))

  if not forfeit_date:
    m_forfeit_b = re.search(
        r"실권주[^\d]{0,80}(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})일?\s*(?:~|-|부터)\s*(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})일?",
        text,
    )
    if m_forfeit_b:
      forfeit_date = to_standard_date(m_forfeit_b.group(1))

  if not forfeit_date:
    m_forfeit_c = re.search(
        r"(?:일반공모|실권주\s*청약)[^\d]{0,50}(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})",
        text,
    )
    if m_forfeit_c:
      forfeit_date = to_standard_date(m_forfeit_c.group(1))

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
        margin-top: 0.8rem;
        margin-bottom: 1.1rem;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

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
# 7. 2대 탭 구성 (일정 등록 / [09402] 입력)
# ==========================================
tab_schedule, tab_system = st.tabs(["📅 일정 등록", "💻 [09402] 입력"])

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
      col_a, col_b = st.columns([4.8, 1.4])
      with col_a:
        date_val = notice.get("date", "")
        date_display = f"📅 `{date_val}`" if date_val else "📅 `일자 미정`"
        st.caption(date_display)
        st.markdown(f"**{notice.get('title', '')}**")
        
      with col_b:
        st.write("")
        do_register = st.button("🚀 일정등록", key=f"btn_sched_{idx}", use_container_width=True)

      if do_register:
        with st.spinner("일정 분석 및 등록 중..."):
          try:
            p = parse_offering_schedule_from_contents(notice.get("contents", ""))
            registered_lines = []

            # 1. 권리락일 등록
            if p.get("ex_rights_date"):
              d_fmt = p["ex_rights_date"].replace("/", "-")
              create_notion_task(f"[{current_code}] 유상증자 (권리락일)", d_fmt)
              registered_lines.append(f"• **권리락일**: {d_fmt}")

            # 2. 신주인수권 상장일 등록
            if p.get("rights_start"):
              d_fmt = p["rights_start"].replace("/", "-")
              create_notion_task(f"[{current_code}] 유상증자 (신주인수권상장)", d_fmt)
              registered_lines.append(f"• **신주인수권상장일**: {d_fmt}")

            # 3. 구주주 청약 시작일 등록
            if p.get("sub_date"):
              d_fmt = p["sub_date"].replace("/", "-")
              create_notion_task(f"[{current_code}] 유상증자 (구주주청약)", d_fmt)
              registered_lines.append(f"• **청약일**: {d_fmt}")

            if registered_lines:
              success_msg = "**✅ 일정 등록 완료!**\n\n" + "\n\n".join(registered_lines)
              st.success(success_msg)
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
# TAB 2: [09402] 입력 UI
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
        st.text_input("실권주청약일", value=data.get("forfeit_date", ""))
        st.text_input("배당기산일", value=data.get("div_start_date", ""))
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
