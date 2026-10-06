import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from notion_client import Client
import requests
import streamlit as st

NOTION_TOKEN = st.secrets["NOTION_TOKEN"]
DATABASE_ID = st.secrets["DATABASE_ID"]
CATEGORY_RELATION_ID = "e409fb1b1f8982df81928157b6bd5374"

notion = Client(auth=NOTION_TOKEN)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://stock.naver.com/",
}

def fetch_naver_notices(stock_code: str):
    url = f"https://stock.naver.com/api/domestic/detail/notice?itemCode={stock_code}&startIdx=0&pageSize=30"
    res = requests.get(url, headers=HEADERS, timeout=10)
    res.raise_for_status()
    data = res.json()

    notices = []
    if isinstance(data, list):
        notices = data
    elif isinstance(data, dict):
        notices = data.get("notices") or data.get("list") or data.get("result") or []

    filtered_list = []
    for item in notices:
        title = item.get("title", "")
        if "유상증자" not in title:
            continue

        raw_dt = item.get("datetime", "")
        date_str = raw_dt.split("T")[0].replace("-", ".") if raw_dt else ""

        corp_match = re.split(r"\(정정\)|유상증자", title)
        corp_name = corp_match[0].strip() if corp_match and corp_match[0] else title

        filtered_list.append({
            "title": title,
            "corp_name": corp_name,
            "no": str(item.get("no", "")),
            "date": date_str,
            "contents": item.get("contents", ""),
        })

    return filtered_list

def parse_offering_schedule_from_contents(html_content: str):
    if not html_content:
        return {}

    soup = BeautifulSoup(html_content, "html.parser")
    text = soup.get_text()

    def to_standard_date(raw_str):
        if not raw_str:
            return ""
        c = raw_str.replace("년", "-").replace("월", "-").replace("일", "").replace(".", "-").replace(" ", "")
        p = c.split("-")
        if len(p) >= 3 and p[0].isdigit() and p[1].isdigit() and p[2].isdigit():
            return f"{p[0]}/{int(p[1]):02d}/{int(p[2]):02d}"
        return ""

    def find_date(keywords):
        for kw in keywords:
            m = re.search(rf"{kw}[^\d]{{0,50}}(\d{{4}}[\.\-년]\s*\d{{1,2}}[\.\-월]\s*\d{{1,2}})", text)
            if m:
                return to_standard_date(m.group(1))
        return ""

    record_date = find_date(["신주배정기준일", "배정기준일"])
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

    issue_price = ""
    m_cf = re.search(r"확정발행가[^\d]{0,40}보통주식[^\d]{0,30}([\d,]+)", text)
    if m_cf and m_cf.group(1).replace(",", "").isdigit():
        issue_price = f"{int(m_cf.group(1).replace(',', '')):,}"
    if not issue_price:
        m_ep = re.search(r"예정발행가[^\d]{0,40}보통주식[^\d]{0,30}([\d,]+)", text)
        if m_ep and m_ep.group(1).replace(",", "").isdigit():
            issue_price = f"{int(m_ep.group(1).replace(',', '')):,}"
    if not issue_price:
        m_fb = re.search(r"(?:신주발행가액|발행가액)[^\d]{0,40}([\d,]+)\s*원", text)
        if m_fb and m_fb.group(1).replace(",", "").isdigit():
            issue_price = f"{int(m_fb.group(1).replace(',', '')):,}"

    applied_ratio = ""
    m_rt = re.search(r"(?:신주배정주식수|신주배정비율)[^\d]{0,30}(\d+\.\d+)", text)
    if m_rt:
        try:
            applied_ratio = f"{float(m_rt.group(1)) * 100:.11f}".rstrip("0") + "%"
        except Exception:
            applied_ratio = m_rt.group(1)

    rights_start = ""
    rights_end = ""
    p_rg = r"신주인수권[^\d]{0,30}(\d{4}[년\.\-]\s*\d{1,2}[월\.\-]\s*\d{1,2}일?)\s*(?:~|-)\s*(\d{4}[년\.\-]\s*\d{1,2}[월\.\-]\s*\d{1,2}일?)"
    m_rg = re.search(p_rg, text)
    if m_rg:
        rights_start = to_standard_date(m_rg.group(1))
        rights_end = to_standard_date(m_rg.group(2))
    if not rights_start:
        rights_start = find_date(["신주인수권증서 상장예정일", "신주인수권증서 상장일", "신주인수권 상장예정일", "신주인수권 상장일"])

    sub_start_date = ""
    sub_end_date = ""
    d_regex = r"(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})"
    for tr in soup.find_all("tr"):
        tr_text = tr.get_text()
        if "구주주" in tr_text:
            dates = re.findall(d_regex, tr_text)
            if dates:
                if not sub_start_date:
                    sub_start_date = to_standard_date(dates[0])
                if len(dates) > 1 and not sub_end_date:
                    sub_end_date = to_standard_date(dates[1])
                break

    if not sub_start_date or not sub_end_date:
        p_sub_range = (
            r"(?:구주주|청약)[^\d]{0,40}"
            r"(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2}일?)"
            r"\s*(?:~|-)\s*"
            r"(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2}일?)"
        )
        m_range = re.search(p_sub_range, text)
        if m_range:
            if not sub_start_date:
                sub_start_date = to_standard_date(m_range.group(1))
            if not sub_end_date:
                sub_end_date = to_standard_date(m_range.group(2))

    if not sub_start_date:
        sub_start_date = find_date(["구주주청약일", "구주주청약", "청약예정일", "청약일"])
    if not sub_end_date:
        m_se = re.search(
            r"(?:구주주[^\d]{0,50})?종료일[^\d]{0,20}(\d{4}[\.\-년]\s*\d{1,2}[월\.\-]\s*\d{1,2})일?",
            text,
        )
        if m_se:
            sub_end_date = to_standard_date(m_se.group(1))

    pay_date = find_date(["12. 납입일", "주금납입일", "납입일"])
    div_start_date = find_date(["14. 신주의 배당기산일", "신주의 배당기산일", "배당기산일"])
    listing_date = find_date(["16. 신주의 상장예정일", "신주의 상장예정일", "신주상장예정일", "신주의 상장"])

    forfeit_date = ""
    for tr in soup.find_all("tr"):
        tr_text = tr.get_text()
        if ("일반공모청약" in tr_text or "실권주" in tr_text) and "구주주" not in tr_text:
            dates = re.findall(d_regex, tr_text)
            if dates:
                forfeit_date = to_standard_date(dates[0])
                break

    if not forfeit_date:
        m_ff = re.search(r"(?:실권주\s*일반공모청약|실권주\s*청약|일반공모청약)[^\d]{0,80}(\d{4}[\.\-년]\s*\d{1,2}[\.\-월]\s*\d{1,2})", text)
        if m_ff:
            forfeit_date = to_standard_date(m_ff.group(1))
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

def create_notion_task(title: str, event_date: str):
    notion.pages.create(
        parent={"database_id": DATABASE_ID},
        properties={
            "이름": {"title": [{"text": {"content": title}}]},
            "category": {"relation": [{"id": CATEGORY_RELATION_ID}]},
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
st.set_page_config(page_title="[국내공시] 유상증자", page_icon="📊", layout="centered")
st.title("📊 [국내공시] 유상증자")

col_in1, col_in2 = st.columns([4, 1.2])
with col_in1:
    stock_code = st.text_input("종목코드 입력", placeholder="예: 448730", label_visibility="collapsed").strip()
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

tab_schedule, tab_system = st.tabs(["📅 일정 등록", "💻 [09402] 입력"])

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

                        if p.get("ex_rights_date"):
                            d_fmt = p["ex_rights_date"].replace("/", "-")
                            create_notion_task(f"[{current_code}] 유상증자 (권리락일)", d_fmt)
                            registered_lines.append(f"• **권리락일**: {d_fmt}")

                        if p.get("rights_start"):
                            d_fmt = p["rights_start"].replace("/", "-")
                            create_notion_task(f"[{current_code}] 유상증자 (신주인수권상장)", d_fmt)
                            registered_lines.append(f"• **신주인수권상장일**: {d_fmt}")

                        if p.get("sub_date"):
                            d_fmt = p["sub_date"].replace("/", "-")
                            create_notion_task(f"[{current_code}] 유상증자 (구주주청약)", d_fmt)
                            registered_lines.append(f"• **청약일**: {d_fmt}")

                        if registered_lines:
                            st.success("**✅ 일정 등록 완료!**\n\n" + "\n\n".join(registered_lines))
                            st.balloons()
                        else:
                            st.warning("공시 본문에서 핵심 일정을 찾지 못했습니다.")
                    except Exception as e:
                        st.error(f"등록 실패: {e}")

            st.write("---")

    elif st.session_state.get("current_code") and not st.session_state.get("found_notices"):
        st.info("해당 종목의 최근 공시 중 유상증자 관련 공시가 없습니다.")
    else:
        st.info("상단에 종목코드(6자리)를 입력하고 [🔍 공시 조회]를 눌러주세요.")

with tab_system:
    if st.session_state.get("found_notices"):
        notices = st.session_state["found_notices"]
        current_code = st.session_state.get("current_code", "")

        options = [f"[{n.get('date', '-')}] {n.get('title', '')}" for n in notices]
        selected_idx = st.selectbox(
            "기준 공시 선택",
            range(len(options)),
            format_func=lambda x: options[x],
            key="system_notice_select",
        )

        selected_notice = notices[selected_idx]
        data = parse_offering_schedule_from_contents(selected_notice.get("contents", ""))

        inst_code_5 = current_code[:5] if len(current_code) >= 5 else current_code
        corp_name = selected_notice.get("corp_name", "")

        st.write("")
        st.markdown("**■ 기본 정보 및 청약/배정 일정**")
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

            st.write("---")

            r3_1, r3_2 = st.columns(2)
            with r3_1:
                st.text_input("권리락일", value=data.get("ex_rights_date", ""))
                st.caption("⚠️ 임시공휴일/휴장일에 따라 권리락일이 달라질 수 있으니 재확인이 필요합니다.")
                st.text_input("발행가", value=data.get("issue_price", ""))
            with r3_2:
                st.text_input("배정기준일", value=data.get("record_date", ""))
                st.text_input("적용비율", value=data.get("applied_ratio", ""))

            st.write("---")

            r4_1, r4_2 = st.columns(2)
            with r4_1:
                st.text_input("청약일", value=data.get("sub_date", ""))
                st.text_input("주금납입일", value=data.get("pay_date", ""))
                st.text_input("주식유통일 (신주상장)", value=data.get("listing_date", ""))
            with r4_2:
                st.text_input("실권주청약일", value=data.get("forfeit_date", ""))
                st.text_input("배당기산일", value=data.get("div_start_date", ""))
                st.selectbox("공시확정", ["여", "부"], index=0)

        st.write("")
        st.markdown("**■ 신주인수권증서 등록 정보**")
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
