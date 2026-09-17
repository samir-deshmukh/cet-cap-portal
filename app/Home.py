"""
app/Home.py — CET CAP Decision Support: 3-input search
(City, Percentage, Course) -> college list -> detail panel.
See docs/ARCHITECTURE.md "Application Layers" and docs/DECISIONS.md
"3-input search: city/percentage/course, list-then-detail UX" for the
full design reasoning behind this screen.

Screen 1 (this page): city, percentage, course only. Results show ONLY
college name, highest cutoff cleared (any category — see caption), and
city. Nothing else is fetched into the visible table.

Screen 2 (st.dialog on row click): category selector (taken as input
HERE, not on screen 1), website link, seat matrix, and a
percentile-vs-year trend chart — all sourced from the SAME data already
loaded for screen 1 (src/cet_cap/queries.search_cutoffs_for_course),
filtered down to one institution_code in pandas. No second cutoffs
query per click. The seat matrix is the one exception that needs its
own query (src/cet_cap/queries.seat_matrix_for_institute), since seat
capacity isn't part of the cutoffs data at all.

Run:
    streamlit run app/Home.py -- --db db/cet_cap.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from cet_cap.db import get_engine  # noqa: E402
from cet_cap.queries import (  # noqa: E402
    available_program_families,
    search_cutoffs_for_course,
    seat_matrix_for_institute,
)
from cet_cap.search import filter_by_city, summarize_colleges  # noqa: E402


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='db/cet_cap.db')
    args, _ = ap.parse_known_args(sys.argv[1:])
    return args


@st.cache_resource
def _engine(db_path_or_url: str):
    return get_engine(db_path_or_url)


@st.cache_data(show_spinner=False)
def _load_background(db_path: str, percentage: float, program_family: str):
    engine = get_engine(db_path)
    return search_cutoffs_for_course(engine, percentage=percentage, program_family=program_family)


@st.dialog('College details', width='large')
def show_college_detail(college_row, college_rows, engine, program_family):
    st.subheader(college_row['institution_name'])
    top = st.columns([3, 1])
    with top[0]:
        st.caption(f"📍 {college_row['city'] or 'City not on record'}")
        if college_row.get('home_university'):
            st.caption(f"Home university: {college_row['home_university']}")
    with top[1]:
        if college_row['website']:
            st.link_button('Visit website', college_row['website'])
        else:
            st.caption('No verified website on record')

    st.divider()

    categories = sorted(college_rows['base_category'].dropna().unique())
    category = st.selectbox('Your category', categories, key='detail_category')
    is_ladies = st.checkbox('Ladies-reserved seat', key='detail_ladies')

    cat_rows = college_rows[
        (college_rows['base_category'] == category)
        & (college_rows['is_ladies'] == int(is_ladies))
    ]

    if cat_rows.empty:
        st.warning(
            f'No historical cutoff at or below your percentage for this college in '
            f'category {category}{" (ladies)" if is_ladies else ""}. '
            'It may still exist under a different category — try another.'
        )
    else:
        st.success(f"{len(cat_rows)} matching historical cutoff record(s), "
                   f"best (toughest) cleared: {cat_rows['cutoff_percentile'].max():.4f}")

        trend = (
            cat_rows.groupby(['year', 'round'])['cutoff_percentile']
            .max()
            .reset_index()
        )
        trend['year_round'] = trend['year'].astype(str) + ' R' + trend['round'].astype(str)
        fig = px.line(
            trend.sort_values(['year', 'round']), x='year_round', y='cutoff_percentile',
            markers=True, labels={'year_round': 'Year / Round', 'cutoff_percentile': 'Cutoff %ile'},
        )
        fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

        st.dataframe(
            cat_rows[['year', 'round', 'stage_code', 'section_code',
                      'cutoff_percentile', 'cutoff_rank']]
            .rename(columns={
                'stage_code': 'Stage', 'section_code': 'Section',
                'cutoff_percentile': 'Cutoff %ile', 'cutoff_rank': 'Cutoff Rank',
            })
            .sort_values(['year', 'round'], ascending=False),
            use_container_width=True, hide_index=True,
        )

    st.divider()
    st.markdown('**Seat matrix** (most recent year on record)')
    seats = seat_matrix_for_institute(engine, college_row['institution_code'], program_family)
    if seats.empty:
        st.caption('No seat-matrix data on record for this institute/course.')
    else:
        totals = seats[seats['is_total'] == 1][['allocation_lane', 'seats']] \
            .rename(columns={'allocation_lane': 'Lane', 'seats': 'Total seats'})
        st.dataframe(totals, use_container_width=True, hide_index=True)
        with st.expander('Full breakdown by category'):
            by_cat = seats[seats['is_total'] == 0][
                ['allocation_lane', 'base_category', 'is_ladies', 'seats']
            ].rename(columns={'allocation_lane': 'Lane', 'base_category': 'Category',
                               'is_ladies': 'Ladies', 'seats': 'Seats'})
            st.dataframe(by_cat, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title='CET CAP Decision Support', layout='wide')
    st.title('CET CAP — Find Your Colleges')
    st.caption(
        'Enter your city, percentage, and course. The list below shows the '
        'toughest historical cutoff you\'d still have cleared at each '
        'college, across every category — not just the category you '
        'belong to. Click a college to pick your actual category and see '
        'the real picture.'
    )

    args = parse_args()
    db_path = Path(args.db)
    if '://' not in args.db and not db_path.exists():
        st.error(
            f'Database not found at `{db_path}`. Run '
            '`scripts/seed_reference_tables.py`, `scripts/ingest.py`, '
            '`scripts/ingest_seats.py`, and `scripts/enrich_institutes.py` '
            'first — see README.md.'
        )
        st.stop()

    engine = _engine(args.db)

    col1, col2, col3 = st.columns(3)
    with col1:
        city = st.text_input('City', placeholder='e.g. Pune')
    with col2:
        percentage = st.number_input(
            'Your CET percentage', min_value=0.0, max_value=100.0,
            value=90.0, step=0.01, format='%.4f',
        )
    with col3:
        program_family = st.selectbox('Course', available_program_families(engine))

    if st.button('Find colleges', type='primary'):
        st.session_state['program_family'] = program_family
        with st.spinner('Loading matching records...'):
            raw = _load_background(str(args.db), percentage, program_family)
        st.session_state['search_raw'] = raw
        st.session_state['search_done'] = True

    if st.session_state.get('search_done'):
        raw = st.session_state['search_raw']
        city_filtered = filter_by_city(raw, city)
        summary = summarize_colleges(city_filtered)

        if summary.empty:
            st.warning(
                'No colleges found for this combination. Try a different '
                'city spelling (it matches against the college\'s listed '
                'city, not a dropdown) or a lower percentage.'
            )
        else:
            st.success(f'{len(summary):,} colleges found.')
            for _, row in summary.iterrows():
                c1, c2, c3, c4 = st.columns([4, 1.5, 1.5, 1])
                c1.markdown(f"**{row['institution_name']}**")
                c2.markdown(f"Cutoff: **{row['highest_cutoff']:.4f}**")
                c3.markdown(f"📍 {row['city'] or 'Unknown'}")
                if c4.button('View', key=f"view_{row['institution_code']}"):
                    college_rows = city_filtered[
                        city_filtered['institution_code'] == row['institution_code']
                    ]
                    show_college_detail(row, college_rows, engine, st.session_state['program_family'])
                st.divider()


if __name__ == '__main__':
    main()
