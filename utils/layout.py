"""Éléments de mise en page communs aux pages (en-tête, rangée de KPI)."""
from __future__ import annotations

from typing import Sequence

import streamlit as st


def page_header(title: str, caption: str = "") -> None:
    """En-tête de page : titre court en casse phrase + une ligne de contexte."""
    st.header(title, anchor=False)
    if caption:
        st.caption(caption)


def kpi_row(items: Sequence[tuple[str, str, str | None]]) -> None:
    """Rangée de KPI (grille fixe) : ``(libellé, valeur, aide en infobulle)``."""
    columns = st.columns(len(items), gap="small")
    for column, (label, value, help_text) in zip(columns, items):
        with column:
            st.metric(label, value, help=help_text, border=True)
