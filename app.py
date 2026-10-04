from datetime import date, timedelta
import io
import re
from urllib.parse import quote
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
  day = target_date - timedelta(days=1)
  while day.weekday() >= 5:  # 5: 토, 6: 일
    day -= timedelta(days=1)
  return day


# ==========================================
# 3. 프록시/직접 연결 지원 요청 헬퍼
# ==========================================
def make_dart_request(target_url: str, params: dict, as_binary: bool = False):
  """DART 해외 IP 방화벽 차단을 우회하기 위해 다중 엔드포인트(직접 -> 우회 프록시) 순차 시도"""
  # 1. 쿼리스트링 조합
  query_str = "&".join(
      [f"{k}={quote(str(v))}" for k, v in params.items()]
  )
  full_target_url = f"{target_url}?{query_str}"

  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
          " like Gecko) Chrome/128.0.0.0 Safari/537.36"
      ),
      "Accept": "*/*",
  }

  endpoints = [
      # (1) 해외 공공 프록시 게이트웨이 1 (AllOrigins - 차단 우회용)
      (
          "proxy_allorigins",
          f"https://api.allorigins.win/raw?url={quote(full_target_url)}",
      ),
      # (2) 해외 공공 프록시 게이트웨이 2 (Corsproxy)
      (
          "proxy_cors",
          f"https://corsproxy.io/?url={quote(full_target_url)}",
      ),
      # (3) 원본 직접 요청
      ("direct", full_target_url),
  ]

  last_error = None
  for mode, req_url in endpoints:
    try:
      res = requests.get(req_url, headers=headers, timeout=8)
      if res.status_code == 200 and len(res.content) > 0:
        return res.content if as_binary else res.json()
    except Exception as e:
      last_error = e
      continue

  raise Exception(f"DART 서버 응답 실패 (방화벽 우회 실패): {last_error}")


# ==========================================
# 4. [1단계] 공시 목록 초경량 수신 (제목/종목명만)
# ==========================================
@st.cache_data(ttl=600, show_spinner=False)
def fetch_disclosure_list(target_date: date):
  bgn_de = target_date.strftime("%Y%m%d")
  url = "https://opendart.fss.or.kr/api/list.json"
  params = {
      "crtfc_key": DART_API_KEY,
      "bgn_de": bgn_de,
      "end_de": bgn_de,
      "pblntf_detail_ty": "B001",
      "page_count": 40,
  }

  data = make_dart_request(url, params, as_binary=False)

  if data.get("status") == "013":
    return []

  if data.get("status") != "000":
    raise Exception(f"DART API 응답 오류: {data.get('message')}")

  items = []
  for item in data.get("list", []):
    report_nm = item.get("report_nm", "")
    if "유상증자" in report_nm:
      items.append({
          "corp_name": item.get("corp_name"),
          "stock_code": item.get("stock_code"),
          "report_nm": report_nm,
          "rcept_no": item.get("rcept_no"),
          "rcept_dt": item.get("rcept_dt"),
      })
  return items


# ==========================================
# 5. [2단계] 선택된 종목만 본문 다운로드 및 일정 추출
# ==========================================
@st.cache_data(ttl=3600, show_spinner=False)
def get_disclosure_text(rcept_no: str) -> str:
  api_url = "https://opendart.fss.or.kr/api/document.xml"
  params = {"crtfc_key": DART_API_KEY, "rcept_no": rcept_no}

  content = make_dart_request(api_url, params, as_binary=True)

  full_text = ""
  try:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
      for filename in z.namelist():
        if filename.endswith(".xml") or filename.endswith(".html"):
          raw_data = z.read(filename)
          soup = BeautifulSoup(raw_data, "html.parser")
          full_text += " " + soup.get_text()
  except zipfile.BadZipFile:
    soup = BeautifulSoup(content, "html.parser")
    msg = soup.find("message")
    err_text = msg.text if msg else content.decode("utf-8", "ignore")[:200]
    raise Exception(f"DART 문서 수신 오류: {err_text}")

  return full_text


def parse_offering_schedule(text: str, corp_name: str):
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
# 6. 노션 데이터베이스 등록
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
# 7. Streamlit UI
# ==========================================
st.set_page_config(
    page_title="[국내공시] 유상증자 등록", page_icon="📊", layout="centered"
)
st.title("📊 [국내공시] 유상증자 등록")
st.write(
    "조회일 기준 유상증자 공시 목록을 가져와 보유 종목만 선택하여 등록합니다."
)

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
if "searched" not in st.session_state:
  st.session_state.searched = False

if search_btn:
  st.session_state.searched = True
  with st.spinner(f"{selected_date.strftime('%Y-%m-%d')} 유상증자 종목 조회 중..."):
    try:
      items = fetch_disclosure_list(selected_date)
      st.session_state.disclosures = items
    except Exception as e:
      st.session_state.disclosures = []
      st.error(f"공시 목록 조회 실패: {e}")

if st.session_state.searched:
  disclosures = st.session_state.disclosures
  if not disclosures:
    st.info(
        f"📅 {selected_date.strftime('%Y-%m-%d')}에 접수된 유상증자 공시가"
        " 없습니다."
    )
  else:
    st.write(f"📋 **확인된 유상증자 종목 {len(disclosures)}건**")

    options = {
        f"[{item['corp_name']}] ({item['stock_code']}) - {item['report_nm']}": (
            item
        )
        for item in disclosures
    }

    selected_labels = st.multiselect(
        "보유 종목 선택",
        options=list(options.keys()),
        placeholder="등록할 종목을 선택하세요...",
    )

    if st.button("🚀 선택 종목 상세 분석 및 노션 등록", type="primary"):
      if not selected_labels:
        st.warning("등록할 종목을 1개 이상 선택해 주세요.")
      else:
        with st.spinner("선택 종목 공시 원문 파싱 및 노션 일정 등록 중..."):
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
              else:
                st.warning(
                    f"⚠️ [{corp}] 본문에서 일정을 추출하지 못했습니다."
                )
            except Exception as e:
              st.error(f"❌ [{corp}] 처리 중 에러: {e}")

          if total_registered > 0:
            st.balloons()
