import io
import re
from urllib.parse import parse_qs, urlparse
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
# 2. 접수번호 추출 및 OpenDART API 파싱
# ==========================================
def extract_rcept_no(input_str: str) -> str:
  """KIND/DART URL 또는 14자리 숫자 접수번호 추출"""
  clean_str = input_str.strip()
  if clean_str.isdigit() and len(clean_str) == 14:
    return clean_str

  parsed = urlparse(clean_str)
  qs = parse_qs(parsed.query)

  if "acptno" in qs:
    return qs["acptno"][0]
  if "rcpNo" in qs:
    return qs["rcpNo"][0]

  match = re.search(r"(\d{14})", clean_str)
  if match:
    return match.group(1)
  return ""


def get_disclosure_text_from_dart(rcept_no: str) -> str:
  """OpenDART API를 통해 공시 원본 xml/html을 받아 텍스트 추출"""
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
    raise Exception(f"DART API 응답 오류: {err_text}")

  return full_text


def parse_offering_schedule(text: str):
  """회사명 및 주요 일정 추출"""
  corp_name = "유상증자"
  corp_match = re.search(r"회\s*사\s*명\s*[:\s]?\s*([가-힣A-Za-z0-9㈜]+)", text)
  if corp_match:
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
# 3. 노션 데이터베이스 카드 생성 함수
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
# 4. Streamlit UI
# ==========================================
st.set_page_config(
    page_title="[국내공시] 유상증자 등록", page_icon="📊", layout="centered"
)
st.title("📊 [국내공시] 유상증자 등록")
st.write(
    "DART 공시 상세 페이지 URL 입력 후 유상증자 일정을 노션 TO DO LIST에 자동"
    " 등록합니다"
)

input_val = st.text_input(
    "DART 공시 URL 입력",
    placeholder="",
)

if st.button("🚀 노션 TO DO LIST에 등록하기", type="primary"):
  if not input_val:
    st.warning("공시 URL 또는 접수번호를 입력해 주세요.")
  else:
    rcept_no = extract_rcept_no(input_val)
    if not rcept_no:
      st.error(
          "입력값에서 공시 접수번호(14자리)를 찾을 수 없습니다. 올바른 링크인지"
          " 확인해 주세요."
      )
    else:
      with st.spinner(
          f"공시({rcept_no}) 분석 및 노션 일정 등록 진행 중..."
      ):
        try:
          raw_text = get_disclosure_text_from_dart(rcept_no)
          data = parse_offering_schedule(raw_text)
          corp = data["corp_name"]
          registered = []

          if data["record_date"]:
            create_notion_task(
                f"[{corp}] 유상증자 신주배정기준일",
                data["record_date"],
                "권리락/배정 기준일 확인",
            )
            registered.append(f"신주배정기준일: {data['record_date']}")

          if data["sub_start"]:
            create_notion_task(
                f"[{corp}] 유상증자 청약 개시",
                data["sub_start"],
                "청약 신청 진행 및 자금 확인",
            )
            registered.append(f"청약개시일: {data['sub_start']}")

          if data["pay_date"]:
            create_notion_task(
                f"[{corp}] 유상증자 주금 납입일",
                data["pay_date"],
                "주금 납입 및 회계처리 확인",
            )
            registered.append(f"납입일: {data['pay_date']}")

          if registered:
            st.success(
                f"✅ [{corp}] 일정 등록 완료!\n- " + "\n- ".join(registered)
            )
          else:
            st.error(
                "공시 원본에서 주요 일정(신주배정기준일, 청약일, 납입일)을 찾지"
                " 못했습니다."
            )
        except Exception as e:
          st.error(f"오류가 발생했습니다: {e}")
