from datetime import date, timedelta
import io
import re
import zipfile
from bs4 import BeautifulSoup
from notion_client import Client
import requests
import streamlit as st

# ==========================================
# 1. 설정 로드 (Streamlit Secrets)
# ==========================================
NOTION_TOKEN = st.secrets["NOTION_TOKEN"]
DATABASE_ID = st.secrets["DATABASE_ID"]
DART_API_KEY = st.secrets["DART_API_KEY"]

notion = Client(auth=NOTION_TOKEN)


# ==========================================
# 2. 영업일 계산 유틸 (직전 1영업일 산출)
# ==========================================
def get_previous_business_day(target_date: date) -> date:
  """기본 주말(토, 일)을 제외한 직전 1영업일 반환 (월요일 -> 전주 금요일)"""
  day = target_date - timedelta(days=1)
  while day.weekday() >= 5:  # 5: 토요일, 6: 일요일
    day -= timedelta(days=1)
  return day


# ==========================================
# 3. OpenDART API 연동
# ==========================================
def fetch_rights_offering_disclosures(target_date: date):
  """해당 일자의 유상증자결정 주요사항보고서 공시 목록 검색"""
  bgn_de = target_date.strftime("%Y%m%d")
  url = "https://opendart.fss.or.kr/api/list.json"
  params = {
      "crtfc_key": DART_API_KEY,
      "bgn_de": bgn_de,
      "end_de": bgn_de,
      "pblntf_ty": "B",  # 주요사항보고서
      "page_count": 100,
  }

  res = requests.get(url, params=params, timeout=10)
  res.raise_for_status()
  data = res.json()

  if data.get("status") != "000":
    # 013: 조회된 데이터 없음
    if data.get("status") == "013":
      return []
    raise Exception(f"DART API 오류: {data.get('message')}")

  items = []
  for item in data.get("list", []):
    report_nm = item.get("report_nm", "")
    # 유상증자 관련 공시 필터링
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
  """OpenDART 문서 원본 XML/HTML 다운로드 및 텍스트 파싱"""
  api_url = "https://opendart.fss.or.kr/api/document.xml"
  params = {"crtfc_key": DART_API_KEY, "rcept_no": rcept_no}

  res = requests.get(api_url, params=params, timeout=15)
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


def parse_offering_schedule(text: str, default_corp: str):
  """공시 본문에서 핵심 일정 3종 추출"""

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
      "corp_name": default_corp,
      "record_date": find_date(["신주배정기준일", "배정기준일"]),
      "sub_start": find_date(
          ["청약예정일", "구주주청약", "청약기간", "청약일"]
      ),
      "pay_date": find_date(["납입일", "주금납입일"]),
  }


# ==========================================
# 4. 노션 데이터베이스 카드 생성 함수
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
st.write("공시 일자 기준 유상증자 공시를 조회하고, 보유 종목을 선택하여 노션에 일괄 등록합니다.")

# 기본값: 직전 1영업일 자동 설정
today = date.today()
default_target_date = get_previous_business_day(today)

col1, col2 = st.columns([2, 1])
with col1:
  selected_date = st.date_input(
      "조회 공시 일자 (기본: 직전 1영업일)",
      value=default_target_date,
      help="월요일 접속 시 전주 금요일이 기본 지정됩니다.",
  )
with col2:
  st.write("")
  st.write("")
  search_btn = st.button("🔍 공시 조회", type="secondary")

# 세션 상태에 공시 목록 보관
if "disclosures" not in st.session_state:
  st.session_state.disclosures = []
if "last_queried_date" not in st.session_state:
  st.session_state.last_queried_date = None

# 날짜 변경 또는 조회 버튼 클릭 시 조회 실행
if (
    search_btn
    or st.session_state.last_queried_date != selected_date
    or not st.session_state.disclosures
):
  with st.spinner(f"{selected_date.strftime('%Y-%m-%d')} 공시 목록 조회 중..."):
    try:
      items = fetch_rights_offering_disclosures(selected_date)
      st.session_state.disclosures = items
      st.session_state.last_queried_date = selected_date
    except Exception as e:
      st.error(f"공시 목록 조회 실패: {e}")

disclosures = st.session_state.disclosures

st.divider()

if not disclosures:
  st.info(
      f"📅 {selected_date.strftime('%Y-%m-%d')} 접수된 유상증자 공시가 없습니다."
  )
else:
  st.write(
      f"📋 **총 {len(disclosures)}건의 유상증자 공시가 확인되었습니다.** (등록할 종목을"
      " 선택하세요)"
  )

  # 선택용 라벨 생성
  options = {
      f"[{item['corp_name']}] ({item['stock_code']}) - {item['report_nm']}": item
      for item in disclosures
  }

  selected_labels = st.multiselect(
      "보유 종목 선택",
      options=list(options.keys()),
      placeholder="우리 펀드/포트폴리오 보유 종목을 선택하세요...",
  )

  if st.button("🚀 선택 종목 노션 TO DO LIST에 일괄 등록", type="primary"):
    if not selected_labels:
      st.warning("등록할 종목을 1개 이상 선택해 주세요.")
    else:
      with st.spinner("선택 종목 공시 원본 분석 및 노션 등록 중..."):
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
                  f"✅ [{corp}] 일정 등록 완료 (기준일:"
                  f" {sched['record_date'] or '-'} / 청약:"
                  f" {sched['sub_start'] or '-'} / 납입:"
                  f" {sched['pay_date'] or '-'})"
              )
              total_registered += 1
            else:
              st.warning(
                  f"⚠️ [{corp}] 공시 본문에서 일정을 파싱하지 못했습니다."
              )
          except Exception as e:
            st.error(f"❌ [{corp}] 등록 중 오류 발생: {e}")

        if total_registered > 0:
          st.balloons()
