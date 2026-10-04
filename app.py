from datetime import date, timedelta
import io
import re
import zipfile
from bs4 import BeautifulSoup
from notion_client import Client
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import streamlit as st

# ==========================================
# 1. 설정 로드 (Streamlit Secrets)
# ==========================================
NOTION_TOKEN = st.secrets["NOTION_TOKEN"]
DATABASE_ID = st.secrets["DATABASE_ID"]
DART_API_KEY = st.secrets["DART_API_KEY"]

notion = Client(auth=NOTION_TOKEN)


# 재시도 및 안정적인 세션 생성 함수
def get_session():
  s = requests.Session()
  retries = Retry(
      total=3,
      backoff_factor=1,
      status_forcelist=[500, 502, 503, 504],
  )
  s.mount("https://", HTTPAdapter(max_retries=retries))
  s.headers.update({
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
          " like Gecko) Chrome/128.0.0.0 Safari/537.36"
      ),
      "Accept": "application/json, text/html, */*",
  })
  return s


# ==========================================
# 2. 영업일 계산 유틸 (직전 1영업일 산출)
# ==========================================
def get_previous_business_day(target_date: date) -> date:
  day = target_date - timedelta(days=1)
  while day.weekday() >= 5:  # 주말 제외
    day -= timedelta(days=1)
  return day


# ==========================================
# 3. OpenDART API 연동
# ==========================================
def fetch_rights_offering_disclosures(target_date: date):
  bgn_de = target_date.strftime("%Y%m%d")
  url = "https://opendart.fss.or.kr/api/list.json"
  params = {
      "crtfc_key": DART_API_KEY,
      "bgn_de": bgn_de,
      "end_de": bgn_de,
      "pblntf_ty": "B",
      "page_count": 100,
  }

  session = get_session()
  # 해외 서버 연결 지연을 고려해 timeout을 30초로 확대
  res = session.get(url, params=params, timeout=30)
  res.raise_for_status()
  data = res.json()

  if data.get("status") != "000":
    if data.get("status") == "013":
      return []
    raise Exception(f"DART API 응답 에러: {data.get('message')}")

  items = []
  for item in data.get("list", []):
    report_nm = item.get("report_nm", "")
    if "유상증자" in report_nm and (
        "결정" in report_nm or "주요사항보고서" in report_nm
    ):
      items.append({
          "corp_name": item.get("corp_name"),
          "stock_code": item.get("stock_code"),
          "report_nm": report_nm,
          "rcept_no": item.get("rcept_no"),
          "rcept_dt": item.get("rcept_dt"),
      })
  return items


def get_disclosure_text(rcept_no: str) -> str:
  api_url = "https://opendart.fss.or.kr/api/document.xml"
  params = {"crtfc_key": DART_API_KEY, "rcept_no": rcept_no}

  session = get_session()
  res = session.get(api_url, params=params, timeout=30)
  res.raise_for_status()

  full_text = ""
  try:
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
      for filename in z.namelist():
        if filename.endswith(".xml") or filename.endswith(".html"):
          raw_data = z.read(filename)
          soup = BeautifulSoup(raw_data, "html.parser")
          full_text += " " + soup.get_text()
  except zipfile.BadZipFile:
    soup = BeautifulSoup(res.content, "html.parser")
    msg = soup.find("message")
    err_text = msg.text if msg else res.text[:200]
    raise Exception(f"DART 문서 수신 오류: {err_text}")

  return full_text


def parse_offering_schedule(text: str, default_corp: str = "유상증자"):
  corp_name = default_corp
  corp_match = re.search(r"회\s*사\s*명\s*[:\s]?\s*([가-힣A-Za-z0-9㈜]+)", text)
  if corp_match and default_corp == "유상증자":
    corp_name = corp_match.group(1).replace("㈜", "").strip()

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
      "corp_name": corp_name,
      "record_date": find_date(["신주배정기준일", "배정기준일"]),
      "sub_start": find_date(
          ["청약예정일", "구주주청약", "청약기간", "청약일"]
      ),
      "pay_date": find_date(["납입일", "주금납입일"]),
  }


# ==========================================
# 4. 노션 데이터베이스 등록
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
# 5. Streamlit UI
# ==========================================
st.set_page_config(
    page_title="[국내공시] 유상증자 등록", page_icon="📊", layout="centered"
)
st.title("📊 [국내공시] 유상증자 등록")

tab1, tab2 = st.tabs(["📅 직전 영업일 공시 조회", "🔗 단건 직접 등록"])

# --- TAB 1: 직전 영업일 자동 목록 조회 ---
with tab1:
  st.caption("조회일 기준 유상증자 공시 목록을 가져와 우리 보유 종목만 선택하여 등록합니다.")

  today = date.today()
  default_target_date = get_previous_business_day(today)

  col1, col2 = st.columns([2, 1])
  with col1:
    selected_date = st.date_input(
        "공시 일자",
        value=default_target_date,
    )
  with col2:
    st.write("")
    st.write("")
    search_btn = st.button("🔍 목록 조회", type="secondary")

  if "disclosures" not in st.session_state:
    st.session_state.disclosures = []

  if search_btn:
    with st.spinner(f"{selected_date.strftime('%Y-%m-%d')} 공시 목록 수신 중..."):
      try:
        items = fetch_rights_offering_disclosures(selected_date)
        st.session_state.disclosures = items
        if not items:
          st.info(
              f"{selected_date.strftime('%Y-%m-%d')}에 접수된 유상증자 결정"
              " 공시가 없습니다."
          )
      except Exception as e:
        st.error(
            f"조회 실패 (DART 서버 지연 발생): {e}\n\n※ 잠시 후 다시 조회하거나,"
            " 급한 건은 [단건 직접 등록] 탭을 이용해 주세요."
        )

  if st.session_state.disclosures:
    disclosures = st.session_state.disclosures
    st.write(f"📋 **확인된 유상증자 공시 {len(disclosures)}건:**")

    options = {
        f"[{item['corp_name']}] ({item['stock_code']}) - {item['report_nm']}": (
            item
        )
        for item in disclosures
    }

    selected_labels = st.multiselect(
        "보유 종목 선택",
        options=list(options.keys()),
        placeholder="등록할 보유 종목을 선택하세요...",
    )

    if st.button("🚀 선택 종목 노션 TO DO LIST에 일괄 등록", type="primary"):
      if not selected_labels:
        st.warning("종목을 1개 이상 선택해 주세요.")
      else:
        with st.spinner("일정 파싱 및 노션 등록 중..."):
          total_registered = 0
          for label in selected_labels:
            target_item = options[label]
            corp = target_item["corp_name"]
            rcept_no = target_item["rcept_no"]

            try:
              raw_text = get_disclosure_text(rcept_no)
              sched = parse_offering_schedule(raw_text, corp)

              sub_count = 0
              if sched["record_date"]:
                create_notion_task(
                    f"[{corp}] 유상증자 신주배정기준일",
                    sched["record_date"],
                    f"권리락/배정 기준일 확인 ({target_item['report_nm']})",
                )
                sub_count += 1
              if sched["sub_start"]:
                create_notion_task(
                    f"[{corp}] 유상증자 청약 개시",
                    sched["sub_start"],
                    f"청약 신청 진행 및 자금 확인 ({target_item['report_nm']})",
                )
                sub_count += 1
              if sched["pay_date"]:
                create_notion_task(
                    f"[{corp}] 유상증자 주금 납입일",
                    sched["pay_date"],
                    f"주금 납입 및 회계처리 확인 ({target_item['report_nm']})",
                )
                sub_count += 1

              if sub_count > 0:
                st.success(
                    f"✅ [{corp}] 등록 완료 (기준일:"
                    f" {sched['record_date'] or '-'} / 청약:"
                    f" {sched['sub_start'] or '-'} / 납입:"
                    f" {sched['pay_date'] or '-'})"
                )
                total_registered += 1
            except Exception as e:
              st.error(f"❌ [{corp}] 등록 오류: {e}")

          if total_registered > 0:
            st.balloons()

# --- TAB 2: 단건 직접 등록 (DART 일시 타임아웃 시 대비 비상구) ---
with tab2:
  st.caption("특정 공시의 DART/KIND URL 또는 접수번호(14자리)를 직접 입력하여 즉시 등록합니다.")
  single_input = st.text_input("공시 URL 또는 14자리 접수번호", placeholder="")

  if st.button("🚀 단건 노션 등록", key="btn_single"):
    if not single_input:
      st.warning("입력값을 확인해 주세요.")
    else:
      m = re.search(r"(\d{14})", single_input.strip())
      if not m:
        st.error("올바른 14자리 공시 접수번호를 찾을 수 없습니다.")
      else:
        r_no = m.group(1)
        with st.spinner(f"접수번호 {r_no} 문서 분석 중..."):
          try:
            raw_text = get_disclosure_text(r_no)
            sched = parse_offering_schedule(raw_text)
            corp = sched["corp_name"]

            cnt = 0
            if sched["record_date"]:
              create_notion_task(
                  f"[{corp}] 유상증자 신주배정기준일",
                  sched["record_date"],
                  "권리락/배정 기준일 확인",
              )
              cnt += 1
            if sched["sub_start"]:
              create_notion_task(
                  f"[{corp}] 유상증자 청약 개시",
                  sched["sub_start"],
                  "청약 신청 진행 및 자금 확인",
              )
              cnt += 1
            if sched["pay_date"]:
              create_notion_task(
                  f"[{corp}] 유상증자 주금 납입일",
                  sched["pay_date"],
                  "주금 납입 및 회계처리 확인",
              )
              cnt += 1

            if cnt > 0:
              st.success(f"✅ [{corp}] 일정 노션 등록 완료!")
            else:
              st.warning("일정 날짜를 추출하지 못했습니다.")
          except Exception as e:
            st.error(f"오류 발생: {e}")
