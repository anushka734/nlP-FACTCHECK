import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import io
import os
import csv
import time
from urllib.parse import urljoin
import plotly.express as px
from dotenv import load_dotenv

# ------------------------------------------------------
# 🌐 Setup and Environment
# ------------------------------------------------------
load_dotenv()
FACT_API_KEY = os.getenv("GOOGLE_FACT_CHECK_API")
FACT_API_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
CACHE_TTL = 24 * 60 * 60
DATA_FILE = "politifact_claims.csv"

# ------------------------------------------------------
# 🧹 Utility: Text Cleaning
# ------------------------------------------------------
def clean_text(text: str) -> str:
    """Standardize input text before API queries."""
    if not isinstance(text, str):
        return ""
    text = re.sub(r'[“”"\'.,!?]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:250]

# ------------------------------------------------------
# 🔍 Google Fact Check Integration
# ------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL)
def get_fact_check_result(statement: str):
    """Fetch fact-check results from Google's API."""
    if not FACT_API_KEY:
        return {"verdict": "API Key Missing", "publisher": None, "rating": None, "url": None}

    query = clean_text(statement)
    if not query:
        return {"verdict": "Unverified", "publisher": None, "rating": None, "url": None}

    params = {"query": query, "key": FACT_API_KEY}
    try:
        res = requests.get(FACT_API_URL, params=params, timeout=10)
        res.raise_for_status()
        data = res.json().get("claims", [])

        for claim in data:
            for review in claim.get("claimReview", []):
                rating_text = review.get("textualRating", "").lower()
                publisher = review.get("publisher", {}).get("name", "Unknown")
                url = review.get("url", "")

                if any(k in rating_text for k in ["false", "misleading", "pants"]):
                    return {"verdict": "False", "publisher": publisher, "rating": rating_text, "url": url}
                if any(k in rating_text for k in ["true", "accurate", "correct", "mostly true"]):
                    return {"verdict": "True", "publisher": publisher, "rating": rating_text, "url": url}
        return {"verdict": "Unverified", "publisher": None, "rating": None, "url": None}

    except requests.RequestException as e:
        return {"verdict": "API Error", "publisher": None, "rating": str(e), "url": None}

# ------------------------------------------------------
# 📰 PolitiFact Scraper
# ------------------------------------------------------
def scrape_politifact(start_date, end_date):
    """Scrape PolitiFact fact-checks for the given date range."""
    base_url = "https://www.politifact.com/factchecks/list/"
    next_page = base_url
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["author", "statement", "source", "date", "label"])

    session = requests.Session()
    page_count, total_rows = 0, 0
    status = st.empty()

    while next_page and page_count < 40:
        page_count += 1
        status.info(f"📄 Fetching page {page_count}... ({total_rows} rows collected)")
        try:
            response = session.get(next_page, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            st.error(f"⚠️ Error fetching data: {e}")
            break

        for card in soup.select("li.o-listicle__item"):
            date_block = card.select_one("div.m-statement__desc")
            if not date_block:
                continue

            match = re.search(r"stated on ([A-Za-z]+\s+\d{1,2},\s+\d{4})", date_block.text)
            if not match:
                continue

            try:
                claim_date = pd.to_datetime(match.group(1))
            except ValueError:
                continue

            if claim_date < start_date:
                next_page = None
                break
            if not (start_date <= claim_date <= end_date):
                continue

            statement_tag = card.select_one("div.m-statement__quote a")
            statement = statement_tag.get_text(strip=True) if statement_tag else None
            source_tag = card.select_one("a.m-statement__name")
            source = source_tag.get_text(strip=True) if source_tag else None
            footer_tag = card.select_one("footer.m-statement__footer")
            author = re.search(r"By\s+([^•]+)", footer_tag.text).group(1).strip() if footer_tag else None
            label_img = card.find("img", alt=True)
            label = label_img["alt"].replace("-", " ").title() if label_img else None

            if statement:
                writer.writerow([author, statement, source, claim_date.strftime("%Y-%m-%d"), label])
                total_rows += 1

        next_btn = soup.find("a", class_="c-button c-button--hollow", string=re.compile("Next", re.I))
        next_page = urljoin(base_url, next_btn["href"]) if next_btn else None
        time.sleep(1)

    buffer.seek(0)
    df = pd.read_csv(buffer).dropna(subset=["statement"])
    df.to_csv(DATA_FILE, index=False)
    return df

# ------------------------------------------------------
# 🤖 Batch Verification
# ------------------------------------------------------
def verify_all(df: pd.DataFrame):
    st.info("🔎 Running Google Fact Check verification...")
    results, progress = [], st.progress(0)

    for i, row in enumerate(df.itertuples(index=False)):
        res = get_fact_check_result(row.statement)
        results.append(res)
        progress.progress((i + 1) / len(df))

    df["google_verdict"] = [r["verdict"] for r in results]
    df["publisher"] = [r["publisher"] for r in results]
    df["google_rating"] = [r["rating"] for r in results]
    df["fact_url"] = [r["url"] for r in results]
    return df

# ------------------------------------------------------
# 📈 Visualization
# ------------------------------------------------------
def show_results(df):
    st.markdown("### 📊 Fact Check Verdict Summary")
    verdict_counts = df["google_verdict"].value_counts(normalize=True).mul(100).round(1)
    fig = px.bar(
        x=verdict_counts.index,
        y=verdict_counts.values,
        text=verdict_counts.values,
        title="Verdict Distribution (%)",
        labels={"x": "Verdict", "y": "Percentage"},
        color=verdict_counts.index,
        color_discrete_sequence=px.colors.qualitative.Safe,
    )
    st.plotly_chart(fig, use_container_width=True)

# ------------------------------------------------------
# 🎨 Custom Styling
# ------------------------------------------------------
CUSTOM_CSS = """
<style>
    [data-testid="stAppViewContainer"] {
        background: linear-gradient(135deg, #e3f2fd 0%, #ede7f6 100%);
        color: #222;
    }
    h1, h2, h3 {
        font-family: 'Poppins', sans-serif;
        color: #2c3e50;
    }
    .stButton > button {
        background: linear-gradient(90deg, #64b5f6, #7986cb);
        color: white;
        border: none;
        border-radius: 10px;
        padding: 0.6em 1.2em;
        font-weight: 600;
        transition: 0.3s;
    }
    .stButton > button:hover {
        background: linear-gradient(90deg, #5e92f3, #5c6bc0);
        transform: scale(1.02);
    }
    .stDataFrame, .stTable {
        border: 1px solid #ccc;
        border-radius: 10px;
        background: white;
    }
    .stDownloadButton > button {
        background: #80cbc4;
        color: white;
        border-radius: 10px;
    }
</style>
"""

# ------------------------------------------------------
# 🚀 Streamlit App
# ------------------------------------------------------
def main():
    st.set_page_config("FactCheck Dashboard", page_icon="📰", layout="wide")
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    st.title("📰 Fact Verification Dashboard")
    st.caption("Cross-check PolitiFact claims using Google Fact Check API")

    st.sidebar.header("⚙️ Configuration")
    start_date = st.sidebar.date_input("Start Date", pd.Timestamp.now() - pd.Timedelta(days=30))
    end_date = st.sidebar.date_input("End Date", pd.Timestamp.now())
    st.sidebar.markdown("---")
    st.sidebar.info("💡 Tip: Scrape recent fact-checks first, then verify them using Google’s Fact Check API.")

    if "data" not in st.session_state:
        st.session_state.data = pd.DataFrame()

    if st.button("🔍 Fetch PolitiFact Claims"):
        df = scrape_politifact(pd.to_datetime(start_date), pd.to_datetime(end_date))
        if df.empty:
            st.warning("No claims found for the selected period.")
        else:
            st.session_state.data = df
            st.success(f"✅ Scraped {len(df)} records successfully!")
            st.dataframe(df, use_container_width=True)
            st.download_button("📥 Download Scraped Data", df.to_csv(index=False), "claims.csv")

    if not st.session_state.data.empty:
        if st.button("🚀 Verify with Google Fact Check"):
            verified_df = verify_all(st.session_state.data)
            show_results(verified_df)
            st.dataframe(verified_df, use_container_width=True)
            st.download_button("📥 Download Verified Results", verified_df.to_csv(index=False), "verified_claims.csv")

if __name__ == "__main__":
    main()
