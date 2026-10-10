from pathlib import Path
from threading import Thread

import streamlit as st
import streamlit.components.v1 as components

from server import create_server


st.set_page_config(page_title="Twin Bishkek — City Futures", page_icon="◉", layout="wide")


@st.cache_resource
def start_simulation_api():
    """Keep the calculation API alive while Streamlit reruns the page."""
    try:
        httpd = create_server()
    except OSError:
        # A separately launched `python server.py` may already own the port.
        return None
    Thread(target=httpd.serve_forever, name="twin-bishkek-api", daemon=True).start()
    return httpd


start_simulation_api()
st.markdown(
    """
    <style>
    html,body,[data-testid="stAppViewContainer"],.stApp {background:#fff!important;margin:0!important;padding:0!important}
    [data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"],footer {display:none!important}
    .block-container {max-width:none!important;padding:0!important;margin:0!important}
    iframe {border:0!important;display:block}
    </style>
    """,
    unsafe_allow_html=True,
)
page = Path(__file__).parent / "frontend" / "index.html"
components.html(page.read_text(encoding="utf-8"), height=930, scrolling=True)
