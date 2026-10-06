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
  # 5. 신주인수권 상장기간 (상장일 ~ 폐지일)
  # rf머트리얼즈 등 "상장 예정기간", "상장예정기간", 콜론(:) 포함 패턴 완벽 지원
  # ------------------------------------------
  rights_start = ""
  rights_end = ""
  rights_period_match = re.search(
      r"신주인수권증서\s*상장\s*(?:예정)?기간[^\d]{0,30}(\d{4}[년\.\-]\s*\d{1,2}[월\.\-]\s*\d{1,2}일?)\s*(?:~|-)\s*(\d{4}[년\.\-]\s*\d{1,2}[월\.\-]\s*\d{1,2}일?)",
      text,
  )
  if rights_period_match:
    rights_start = to_standard_date(rights_period_match.group(1))
    rights_end = to_standard_date(rights_period_match.group(2))

  if not rights_start:
    rights_period_match2 = re.search(
        r"신주인수권\s*상장\s*(?:예정)?기간[^\d]{0,30}(\d{4}[년\.\-]\s*\d{1,2}[월\.\-]\s*\d{1,2}일?)\s*(?:~|-)\s*(\d{4}[년\.\-]\s*\d{1,2}[월\.\-]\s*\d{1,2}일?)",
        text,
    )
    if rights_period_match2:
      rights_start = to_standard_date(rights_period_match2.group(1))
      rights_end = to_standard_date(rights_period_match2.group(2))

  if not rights_start:
    rights_start = find_date([
        "신주인수권증서 상장예정일",
        "신주인수권증서 상장일",
        "신주인수권 상장예정일",
        "신주인수권 상장일",
    ])

  # ------------------------------------------
  # 6. 구주주 청약일정 (시작일, 종료일)
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
        r"구주주[^\d]{0,40}시작일[^\d]{0,20}(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})일?[^\d]{0,4
