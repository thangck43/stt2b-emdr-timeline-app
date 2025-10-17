import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, dcc, html, Input, Output, State, callback_context, dash_table
from dash.exceptions import PreventUpdate
import datetime
import json
import base64
import logging
import os
from flask import request, Response, send_file
from io import BytesIO, StringIO
import xlsxwriter # Thư viện cần thiết cho việc ghi file Excel XLSX
import zipfile

# ===== Cấu hình file Excel =====
FILE_PATH = "STTP2B-EMDR_TrichXuat.xlsx"

# Định nghĩa map màu sắc cho các Stage để đảm bảo tính nhất quán
STAGE_COLOR_MAP = {
    'Start': '#4CAF50', # Xanh lá
    'IDC': '#FF9800',   # Vàng cam
    'IFR': '#2196F3',   # Xanh dương
    'IFA': '#9C27B0',   # Tím
    'AFC': '#F44336'    # Đỏ
}

# Màu sắc thống nhất cho Plan và Actual
TYPE_COLOR_MAP = {
    'Plan': '#2196F3',
    'Actual': '#F44336'
}

# Định nghĩa map màu sắc cho các Discipline (Mới)
DISCIPLINE_COLOR_MAP = {
    'EL': '#FF5733',  # Đỏ cam
    'MECH': '#33FF57', # Xanh lá tươi
    'PROC': '#3357FF', # Xanh dương đậm
    'STR': '#FF33A1',  # Hồng đậm
}

# ==== Hàm load & xử lý dữ liệu ====
def load_data():
    """Load, process data from Excel, and handle FileNotFoundError."""
    # Allow overriding file path via environment for testing/deployment
    file_path = os.environ.get('STTP_FILE_PATH', FILE_PATH)
    try:
        df = pd.read_excel(file_path)
        logging.info(f"Đã load file thành công: {file_path}")
    except FileNotFoundError:
        logging.error(f"Lỗi: Không tìm thấy file tại đường dẫn {FILE_PATH}. Vui lòng kiểm tra lại.")
        empty_summary = pd.DataFrame(columns=['Month-Year', 'Discipline', 'Stage', 'Type', 'Count'])
        empty_pivot = pd.DataFrame(columns=['Month-Year', 'Stage', 'Plan', 'Actual', 'Delta'])
        empty_gantt = pd.DataFrame(columns=['Doc No','Doc Name','Discipline','Doc Type', 'Stage', 'Date', 'Type', 'End'])
        return empty_summary, empty_pivot, empty_gantt
    except Exception as e:
        logging.error(f"Lỗi khi đọc file Excel: {e}")
        empty_summary = pd.DataFrame(columns=['Month-Year', 'Discipline', 'Stage', 'Type', 'Count'])
        empty_pivot = pd.DataFrame(columns=['Month-Year', 'Stage', 'Plan', 'Actual', 'Delta'])
        empty_gantt = pd.DataFrame(columns=['Doc No','Doc Name','Discipline','Doc Type', 'Stage', 'Date', 'Type', 'End'])
        return empty_summary, empty_pivot, empty_gantt

    date_cols = ['P.Start','A.Start','P.IDC','A.IDC','P.IFR','A.IFR','P.IFA','A.IFA','P.AFC','A.AFC']
    EXCEL_DATE_ORIGIN = pd.Timestamp('1899-12-30')
    
    for col in date_cols:
        if col in df.columns:
            try:
                df[col] = pd.to_timedelta(df[col], unit='D', errors='coerce') + EXCEL_DATE_ORIGIN
            except:
                df[col] = pd.to_datetime(df[col], errors='coerce')
        else:
            df[col] = pd.NaT 

    plan_cols = [c for c in df.columns if c.startswith('P.')]
    actual_cols = [c for c in df.columns if c.startswith('A.')]

    plan_melt = df.melt(
        id_vars=['Doc No','Doc Name','Discipline','Doc Type'],
        value_vars=plan_cols, var_name='Stage', value_name='Date'
    ).dropna(subset=['Date'])
    plan_melt['Type'] = 'Plan'
    plan_melt['Stage'] = plan_melt['Stage'].str.replace('P.', '', regex=False)

    actual_melt = df.melt(
        id_vars=['Doc No','Doc Name','Discipline','Doc Type'],
        value_vars=actual_cols, var_name='Stage', value_name='Date'
    ).dropna(subset=['Date'])
    actual_melt['Type'] = 'Actual'
    actual_melt['Stage'] = actual_melt['Stage'].str.replace('A.', '', regex=False)

    merged = pd.concat([plan_melt, actual_melt], ignore_index=True)
    merged['Month-Year'] = merged['Date'].dt.to_period('M').astype(str)

    summary = merged.groupby(['Month-Year','Discipline','Stage','Type']).size().reset_index(name='Count')

    total_counts = merged.groupby(['Month-Year','Stage','Type']).size().reset_index(name='Count')
    
    pivot = total_counts.pivot_table(index=['Month-Year','Stage'], columns='Type', values='Count', fill_value=0).reset_index()

    if 'Actual' not in pivot.columns:
        pivot['Actual'] = 0
    if 'Plan' not in pivot.columns:
        pivot['Plan'] = 0
        
    pivot['Delta'] = pivot['Actual'] - pivot['Plan']

    gantt_data = merged.copy()
    gantt_data['End'] = gantt_data['Date']

    return summary, pivot, gantt_data

# ==== Thiết lập Dash app ====
app = Dash(__name__)
app.title = "STTP2B EMDR Dashboard"

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# Default graph config used for all dcc.Graph components
DEFAULT_GRAPH_CONFIG = {
    'displayModeBar': True,
    'scrollZoom': True,
    # Add useful modebar buttons: zoom, pan, select, lasso, reset, and download as png
    'modeBarButtonsToAdd': ['drawline', 'drawopenpath', 'drawclosedpath', 'eraseshape'],
    'toImageButtonOptions': {
        'format': 'png',
        'filename': 'sttp2b_emdr_chart',
        'height': 800,
        'width': 1200,
        'scale': 1
    }
}

app.layout = html.Div([
    dcc.Store(id='data-store'),

    html.H2("STTP2B EMDR Dashboard: Phân tích tiến độ Tài liệu", style={'textAlign':'center', 'marginBottom': '20px', 'color': '#1f2937'}),
    
    html.Div([
        html.Button("🔄 Tải lại dữ liệu từ Excel", id='refresh-button', 
                    n_clicks=0, 
                    style={'padding': '10px 15px', 'backgroundColor': '#3b82f6', 'color': 'white', 
                           'border': 'none', 'borderRadius': '0.5rem', 'cursor': 'pointer', 'marginRight': '10px'}),
        
        # Nút Export Data
        html.A(
            html.Button("⬇️ Xuất Summary Excel", id='export-button', 
                        style={'padding': '10px 15px', 'backgroundColor': '#10b981', 'color': 'white', 
                               'border': 'none', 'borderRadius': '0.5rem', 'cursor': 'pointer'}),
            id="download-link",
            download="sttp2b_emdr_summary.xlsx",
            href="",
            target="_blank"
        ),
        html.A(
            html.Button("🔗 Download HTML snapshots", id='export-html-button',
                        style={'padding': '10px 15px', 'backgroundColor': '#8b5cf6', 'color': 'white',
                               'border': 'none', 'borderRadius': '0.5rem', 'cursor': 'pointer', 'marginLeft':'10px'}),
            href='/export/html',
            target='_blank'
        ),
        
        html.Span(id='last-update', style={'marginLeft':'20px', 'color':'#6b7280', 'fontStyle': 'italic'})
    ], style={'textAlign': 'center', 'marginBottom': '30px'}),

    # Small controls: toggle delta labels and dragmode
    html.Div([
        html.Div([
            html.Label("Delta Labels:", style={'fontWeight':'bold'}),
            dcc.Checklist(id='delta-labels-toggle',
                          options=[{'label':' Segment labels','value':'segment'},{'label':' Total labels','value':'total'}],
                          value=['segment','total'],
                          inline=True)
        ], style={'display':'inline-block', 'marginRight':'30px'}),

        html.Div([
            html.Label("Drag mode:", style={'fontWeight':'bold'}),
            dcc.RadioItems(id='dragmode', options=[{'label':'Zoom','value':'zoom'},{'label':'Pan','value':'pan'}], value='zoom', inline=True)
        ], style={'display':'inline-block'})
    ], style={'textAlign': 'center', 'marginBottom': '20px'}),

    # Label threshold control: hide segment labels for small absolute values
    html.Div([
        html.Label("Label threshold (hide segment labels if |value| <):", style={'marginRight':'8px'}),
        dcc.Input(id='label-threshold', type='number', value=1, min=0, step=1, style={'width':'80px'})
    ], style={'textAlign':'center', 'marginBottom':'20px'}),

    # --- Khu vực Lọc ---
    html.Div([
        html.Div([
            html.Label("Lọc Discipline:", style={'fontWeight': 'bold', 'display': 'block', 'marginBottom': '5px'}),
            dcc.Dropdown(id='filter-discipline',
                         multi=True,
                         placeholder="Tất cả Discipline",
                         style={'borderRadius': '0.5rem'})
        ], style={'width':'30%', 'display':'inline-block', 'marginRight': '3%'}),
        
        html.Div([
            html.Label("Lọc Stage:", style={'fontWeight': 'bold', 'display': 'block', 'marginBottom': '5px'}),
            dcc.Dropdown(id='filter-stage',
                         multi=True,
                         placeholder="Tất cả Stage",
                         style={'borderRadius': '0.5rem'})
        ], style={'width':'30%', 'display':'inline-block', 'marginRight': '3%'}),
        
        html.Div([
            html.Label("Lọc Loại:", style={'fontWeight': 'bold', 'display': 'block', 'marginBottom': '5px'}),
            dcc.Checklist(id='filter-type',
                          options=[{'label':' Plan','value':'Plan'}, {'label':' Actual','value':'Actual'}],
                          value=['Plan','Actual'],
                          inline=True,
                          labelStyle={'marginRight': '15px'})
        ], style={'width':'30%', 'display':'inline-block'}),
    ], style={'padding': '10px 0', 'borderBottom': '1px solid #e5e7eb', 'marginBottom': '20px'}),

    # --- BIỂU ĐỒ 1: DISCIPLINE TIMELINE (Màu theo Discipline, Pattern theo Type) ---
    html.H3("1. Timeline: Tổng tiến độ theo Discipline (Overview)", style={'marginTop':'40px', 'color': '#1f2937'}),
    html.P("Mỗi Discipline một màu. Pattern (sọc/đặc) phân biệt Plan vs Actual. (Hiển thị tốt nhất cho Overview)", style={'color':'#6b7280'}),
    html.Button('🖥️ Fullscreen', id='fullscreen-discipline', n_clicks=0, style={'marginBottom':'8px'}),
    dcc.Graph(id='discipline-timeline-chart', config=DEFAULT_GRAPH_CONFIG),
    
    # --- BIỂU ĐỒ 2: STAGE BREAKDOWN TIMELINE (Gộp Stage) ---
    html.H3("2. Timeline: So sánh Plan vs Actual theo Giai đoạn", style={'marginTop':'40px', 'color': '#1f2937'}),
    html.P("So sánh Plan vs Actual cạnh nhau, chia theo màu sắc của Stage và pattern của Loại.", style={'color':'#6b7280'}),
    html.Button('🖥️ Fullscreen', id='fullscreen-stage', n_clicks=0, style={'marginBottom':'8px'}),
    dcc.Graph(id='stage-timeline-chart', config=DEFAULT_GRAPH_CONFIG),

    # --- BIỂU ĐỒ 3: DELTA (Stacked, có Label) ---
    html.H3("3. Delta (Actual − Plan)", style={'marginTop':'40px', 'color': '#1f2937'}),
    html.P("Sự chênh lệch số lượng tài liệu giữa Actual và Plan theo từng Stage và tháng. Có hiển thị giá trị Delta tổng.", style={'color':'#6b7280'}),
    html.Button('🖥️ Fullscreen', id='fullscreen-delta', n_clicks=0, style={'marginBottom':'8px'}),
    dcc.Graph(id='delta-chart', config=DEFAULT_GRAPH_CONFIG),
    
    # --- BIỂU ĐỒ 4: GANTT CHART (COLOR-CODED BY STAGE) ---
    html.H3("4. Gantt Chart: Các mốc Tài liệu theo Stage", style={'marginTop':'40px', 'color': '#1f2937'}),
    html.P("Màu sắc của mỗi điểm mốc thể hiện Stage của tài liệu. Ký hiệu tròn: Plan, Kim cương: Actual.", style={'color':'#6b7280'}),
    html.Button('🖥️ Fullscreen', id='fullscreen-gantt', n_clicks=0, style={'marginBottom':'8px'}),
    dcc.Graph(id='gantt-chart', config=DEFAULT_GRAPH_CONFIG),

    # Hidden divs used as outputs for clientside callbacks
    html.Div(id='fs-dummy-discipline', style={'display':'none'}),
    html.Div(id='fs-dummy-stage', style={'display':'none'}),
    html.Div(id='fs-dummy-delta', style={'display':'none'}),
    html.Div(id='fs-dummy-gantt', style={'display':'none'}),
    
    # Detailed Summary Table
    html.H3("5. Detailed Monthly Summary Table", style={'marginTop':'40px', 'color': '#1f2937'}),
    html.P("Bảng chi tiết số lượng Plan/Actual theo từng tháng", style={'color':'#6b7280'}),
    html.Button('🖥️ Fullscreen', id='fullscreen-table', n_clicks=0, style={'marginBottom':'8px'}),
    html.Div(id='detailed-table'),

    # --- Monthly Table (already present above) ---

    # --- Weekly Table ---
    # Add a header and container for the weekly table
    html.H3("6. Detailed Weekly Summary Table", style={'marginTop':'40px', 'color': '#1f2937'}),
    html.P("Bảng chi tiết số lượng Plan/Actual theo từng tuần", style={'color':'#6b7280'}),
    html.Div(id='weekly-table')
    # Fullscreen overlay (hidden by default). We'll copy the chart figure into this graph when user requests fullscreen.
    ,html.Div(id='overlay', style={'display':'none'}, children=[
        html.Div([
            html.Button('✖ Close', id='overlay-close', n_clicks=0, style={'float':'right','padding':'8px 12px','marginBottom':'8px','backgroundColor':'#ef4444','color':'white','border':'none','borderRadius':'6px','cursor':'pointer'}),
            dcc.Graph(id='fullscreen-overlay-chart', config={**DEFAULT_GRAPH_CONFIG, 'scrollZoom': True}, style={'height':'92vh'})
        ], style={'width':'100%','height':'100%','backgroundColor':'white','padding':'10px','boxSizing':'border-box'})
    ])
], style={'maxWidth': '1200px', 'margin': '0 auto', 'padding': '20px', 'fontFamily': 'Inter, sans-serif'})

# ==== Callback 1: Tải dữ liệu và lưu vào dcc.Store (chỉ chạy khi refresh) ====
@app.callback(
    Output('data-store', 'data'),
    Output('filter-stage', 'options'),
    Output('filter-stage', 'value'),
    Output('filter-discipline', 'options'),
    Output('filter-discipline', 'value'),
    Output('last-update', 'children'),
    Input('refresh-button', 'n_clicks')
)
def refresh_data(n_clicks):
    summary, pivot, gantt = load_data()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if summary.empty:
        return {}, [], [], [], [], f"Cập nhật: {now} (Lỗi: Không tìm thấy file hoặc dữ liệu rỗng)"

    stages = sorted(summary['Stage'].unique())
    stage_options = [{'label': s, 'value': s} for s in stages]
    stage_value = stages.copy() 
    
    disciplines = sorted(summary['Discipline'].unique())
    discipline_options = [{'label': d, 'value': d} for d in disciplines]
    discipline_value = disciplines.copy() 
    
    data_store_output = {
        'summary': summary.to_json(orient='split', date_format='iso'),
        'pivot': pivot.to_json(orient='split', date_format='iso'),
        'gantt': gantt.to_json(orient='split', date_format='iso')
    }
    
    return data_store_output, stage_options, stage_value, discipline_options, discipline_value, f"Cập nhật lần cuối: {now}"


# --- Basic HTTP auth (optional) ---
_DASH_USER = os.environ.get('DASH_USER')
_DASH_PASS = os.environ.get('DASH_PASS')

def _check_auth_header(auth_header: str) -> bool:
    if not auth_header or not auth_header.startswith('Basic '):
        return False
    try:
        b64 = auth_header.split(' ', 1)[1]
        decoded = base64.b64decode(b64).decode('utf-8')
        user, pwd = decoded.split(':', 1)
        return user == _DASH_USER and pwd == _DASH_PASS
    except Exception:
        return False


@app.server.before_request
def _require_basic_auth():
    # If no credentials configured, skip auth
    if not (_DASH_USER and _DASH_PASS):
        return None
    # Allow some public endpoints if needed
    public_paths = ['/health']
    if request.path in public_paths:
        return None
    auth_header = request.headers.get('Authorization')
    if not _check_auth_header(auth_header):
        return Response('Authentication required', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})


# --- Export endpoint: generate interactive HTMLs and return as ZIP ---
@app.server.route('/export/html')
def export_html():
    try:
        summary, pivot, gantt = load_data()
        # Recreate simple figures (reuse logic similar to update_charts)
        # Discipline
        df_sum = summary
        if df_sum.empty:
            return Response('No data available', status=204)

        all_months = sorted(df_sum['Month-Year'].unique())

        df_discipline_summary = df_sum.groupby(['Month-Year', 'Discipline', 'Type']).agg(Count=('Count','sum')).reset_index()
        color_map_disc_final = {d: DISCIPLINE_COLOR_MAP.get(d, px.colors.qualitative.Plotly[i % len(px.colors.qualitative.Plotly)]) for i,d in enumerate(df_discipline_summary['Discipline'].unique())}
        fig_discipline = px.bar(df_discipline_summary, x='Month-Year', y='Count', color='Discipline', pattern_shape='Type', barmode='group', text='Count', color_discrete_map=color_map_disc_final)

        # Stage
        df_stage_summary = df_sum.groupby(['Month-Year','Stage','Type']).agg(Count=('Count','sum')).reset_index()
        fig_stage = px.bar(df_stage_summary, x='Month-Year', y='Count', color='Stage', pattern_shape='Type', barmode='group', text='Count', color_discrete_map=STAGE_COLOR_MAP)

        # Delta
        df_pivot = pivot
        if 'Delta' not in df_pivot.columns:
            df_pivot['Delta'] = 0
        stages = list(df_pivot['Stage'].unique())
        fig_delta = go.Figure()
        for stage in stages:
            df_stage_delta = df_pivot[df_pivot['Stage']==stage].set_index('Month-Year')
            y_values = [int(df_stage_delta.loc[m]['Delta']) if m in df_stage_delta.index else 0 for m in all_months]
            fig_delta.add_trace(go.Bar(x=all_months, y=y_values, name=stage, marker=dict(color=STAGE_COLOR_MAP.get(stage))))
        df_total_delta = df_pivot.groupby('Month-Year')['Delta'].sum().reset_index()
        fig_delta.add_trace(go.Scatter(x=df_total_delta['Month-Year'], y=df_total_delta['Delta'], mode='text', text=df_total_delta['Delta'].apply(lambda x: f'{x:+}'), textposition='top center'))

        # Gantt (simple markers)
        df_g = gantt
        fig_g = go.Figure()
        if not df_g.empty:
            unique_doc_nos = sorted(df_g['Doc No'].unique(), reverse=True)
            for stage in df_g['Stage'].unique():
                df_stage = df_g[df_g['Stage']==stage]
                for t in df_stage['Type'].unique():
                    df_plot = df_stage[df_stage['Type']==t]
                    fig_g.add_trace(go.Scatter(x=df_plot['Date'], y=df_plot['Doc No'], mode='markers', name=f'{stage} ({t})'))

        # Write figures to HTML in memory and zip
        memory_file = BytesIO()
        with zipfile.ZipFile(memory_file, mode='w') as zf:
            bio = BytesIO(); zf.writestr('discipline.html', fig_discipline.to_html(include_plotlyjs='cdn'))
            zf.writestr('stage.html', fig_stage.to_html(include_plotlyjs='cdn'))
            zf.writestr('delta.html', fig_delta.to_html(include_plotlyjs='cdn'))
            zf.writestr('gantt.html', fig_g.to_html(include_plotlyjs='cdn'))
        memory_file.seek(0)
        return send_file(memory_file, mimetype='application/zip', as_attachment=True, download_name='sttp2b_charts_html.zip')
    except Exception as e:
        logging.exception('Export HTML failed')
        return Response(f'Export failed: {e}', status=500)

# ==== Callback 3: Export Data (FIX LỖI KHÔNG MỞ ĐƯỢC FILE) ====
@app.callback(
    Output('download-link', 'href'),
    Input('export-button', 'n_clicks'),
    State('data-store', 'data'),
    State('filter-stage', 'value'),
    State('filter-type', 'value'),
    State('filter-discipline', 'value'),
    prevent_initial_call=True
)
def export_summary_excel(n_clicks, data_json, sel_stages, sel_types, sel_disciplines):
    if not data_json:
        return ""

    df_sum = pd.read_json(data_json['summary'], orient='split')
    
    # Áp dụng các bộ lọc hiện tại trên dashboard
    df_filtered = df_sum.copy()
    if sel_stages:
        df_filtered = df_filtered[df_filtered['Stage'].isin(sel_stages)]
    if sel_types:
        df_filtered = df_filtered[df_filtered['Type'].isin(sel_types)]
    if sel_disciplines:
        df_filtered = df_filtered[df_filtered['Discipline'].isin(sel_disciplines)]

    excel_buffer = BytesIO()
    # FIX: Thêm engine='xlsxwriter' để đảm bảo tính tương thích và khắc phục lỗi mở file
    df_filtered.to_excel(excel_buffer, index=False, sheet_name='Summary_Filtered', engine='xlsxwriter')
    excel_buffer.seek(0)
    
    data_b64 = base64.b64encode(excel_buffer.read()).decode()
    return f"data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{data_b64}"


# ==== Callback 2: Cập nhật biểu đồ khi lọc Stage/Type hoặc khi data-store thay đổi ====
@app.callback(
    Output('discipline-timeline-chart', 'figure'),
    Output('stage-timeline-chart', 'figure'), 
    Output('delta-chart', 'figure'),
    Output('gantt-chart', 'figure'),
    Output('detailed-table', 'children'),
    Output('weekly-table', 'children'),
    Input('filter-stage', 'value'),
    Input('filter-type', 'value'),
    Input('filter-discipline', 'value'),
    Input('data-store', 'data'),
    Input('delta-labels-toggle', 'value'),
    Input('label-threshold', 'value'),
    Input('dragmode', 'value')
)
def update_charts(sel_stages, sel_types, sel_disciplines, data_json, delta_label_opts, label_threshold, dragmode):
    if not data_json:
        return go.Figure(), go.Figure(), go.Figure(), go.Figure(), [], []

    # Use StringIO to read JSON literal strings without FutureWarning
    df_sum = pd.read_json(StringIO(data_json['summary']), orient='split')
    df_pivot = pd.read_json(StringIO(data_json['pivot']), orient='split')
    df_g = pd.read_json(StringIO(data_json['gantt']), orient='split')
    
    # Defensive parsing of date columns; catch and report parsing errors early
    try:
        df_g['Date'] = pd.to_datetime(df_g['Date'])
        df_g['End'] = pd.to_datetime(df_g['End'])
    except Exception:
        import logging, traceback
        logging.exception('Failed to parse Date/End columns in gantt data')
        tb = traceback.format_exc()
        err_div = html.Div(children=[
            html.H4('Có lỗi khi phân tích dữ liệu ngày', style={'color':'#b91c1c'}),
            html.Pre(tb, style={'color':'#b91c1c', 'whiteSpace': 'pre-wrap'})
        ])
        return go.Figure(), go.Figure(), go.Figure(), go.Figure(), [err_div], [err_div]

    all_months = sorted(df_sum['Month-Year'].unique())

    # --- Lọc dữ liệu ---
    df_sum_filtered = df_sum.copy()
    if sel_stages:
        df_sum_filtered = df_sum_filtered[df_sum_filtered['Stage'].isin(sel_stages)]
    if sel_types:
        df_sum_filtered = df_sum_filtered[df_sum_filtered['Type'].isin(sel_types)]
    if sel_disciplines:
        df_sum_filtered = df_sum_filtered[df_sum_filtered['Discipline'].isin(sel_disciplines)]

    df_delta_filtered = df_pivot.copy()
    if sel_stages:
        df_delta_filtered = df_delta_filtered[df_delta_filtered['Stage'].isin(sel_stages)]
    
    df_g_filtered = df_g.copy()
    if sel_stages:
        df_g_filtered = df_g_filtered[df_g_filtered['Stage'].isin(sel_stages)]
    if sel_types:
        df_g_filtered = df_g_filtered[df_g_filtered['Type'].isin(sel_types)]
    if sel_disciplines:
        df_g_filtered = df_g_filtered[df_g_filtered['Discipline'].isin(sel_disciplines)]


    # 1. Biểu đồ Discipline Timeline (Đã FIX: Màu theo Discipline, Pattern theo Type)
    df_discipline_summary = df_sum_filtered.groupby(['Month-Year', 'Discipline', 'Type']).agg(
        Count=('Count', 'sum')
    ).reset_index()
    # sanitize
    if 'Count' in df_discipline_summary.columns:
        df_discipline_summary['Count'] = df_discipline_summary['Count'].fillna(0).astype(int)
    
    # Tạo map màu động cho Discipline — fallback to a stable qualitative palette
    qualitative_palette = px.colors.qualitative.Plotly
    disciplines_unique = list(df_discipline_summary['Discipline'].unique())
    color_map_disc_final = {
        d: DISCIPLINE_COLOR_MAP.get(d, qualitative_palette[i % len(qualitative_palette)])
        for i, d in enumerate(disciplines_unique)
    }

    # 1. Biểu đồ Discipline Timeline (Màu theo Discipline, Pattern theo Type)
    fig_discipline = px.bar(
        df_discipline_summary,
        x='Month-Year', y='Count',
        color='Discipline',
        pattern_shape='Type',
        barmode='group',
        text='Count',
        hover_data=['Discipline', 'Type', 'Count', 'Month-Year'],
        color_discrete_map=color_map_disc_final
    )
    fig_discipline.update_traces(texttemplate='%{text}', textposition='outside', selector=dict(type='bar'))
    fig_discipline.update_layout(
        xaxis={'categoryorder':'array', 'categoryarray': all_months},
        margin=dict(t=50, b=20, l=20, r=20),
        legend_title_text="Discipline (Màu) | Loại (Pattern)",
        height=450,
        hovermode='x unified',
        dragmode=dragmode,
        title={
            'text': "1. Timeline: Tổng tiến độ theo Discipline",
            'x': 0.5,
            'xanchor': 'center'
        }
    )

    # 2. Prepare Stage summary used for stage breakdown chart
    df_stage_summary = df_sum_filtered.groupby(['Month-Year', 'Stage', 'Type']).agg(
        Count=('Count', 'sum')
    ).reset_index()
    if 'Count' in df_stage_summary.columns:
        df_stage_summary['Count'] = df_stage_summary['Count'].fillna(0).astype(int)

    # Build stacked bar chart manually so each stage segment shows its own Delta label
    fig_delta = go.Figure()

    # Ensure delta columns exist and are numeric
    for c in ['Plan', 'Actual', 'Delta']:
        if c in df_delta_filtered.columns:
            df_delta_filtered[c] = pd.to_numeric(df_delta_filtered[c], errors='coerce').fillna(0).astype(int)
        else:
            df_delta_filtered[c] = 0

    # Compute total delta per month for adding the total label
    df_total_delta = df_delta_filtered.groupby('Month-Year')['Delta'].sum().reset_index()

    # Ensure 'Stage' and 'Delta' exist; df_delta_filtered expected to have columns: Month-Year, Stage, Plan, Actual, Delta
    stages = list(df_delta_filtered['Stage'].unique())

    # Determine threshold
    try:
        threshold = abs(int(label_threshold)) if label_threshold is not None else 0
    except Exception:
        threshold = 0

    for stage in stages:
        df_stage_delta = df_delta_filtered[df_delta_filtered['Stage'] == stage].set_index('Month-Year')
        # Align to all months and ensure integers
        y_values = []
        for m in all_months:
            if m in df_stage_delta.index and pd.notnull(df_stage_delta.loc[m, 'Delta']):
                try:
                    yv = int(df_stage_delta.loc[m, 'Delta'])
                except Exception:
                    yv = 0
            else:
                yv = 0
            y_values.append(yv)

        # Prepare visible signed labels based on threshold
        if delta_label_opts and 'segment' in delta_label_opts:
            visible_signed = [f'{v:+}' if (v != 0 and abs(v) >= threshold) else '' for v in y_values]
        else:
            visible_signed = [''] * len(y_values)

        fig_delta.add_trace(go.Bar(
            x=all_months,
            y=y_values,
            name=stage,
            marker=dict(color=STAGE_COLOR_MAP.get(stage)),
            text=visible_signed,
            textposition='inside' if (delta_label_opts and 'segment' in delta_label_opts) else 'none',
            texttemplate='%{text}'
        ))

    # Add total Delta labels above the bars
    # Add total Delta labels above the bars (sanitize values)
    df_total_delta['Delta'] = pd.to_numeric(df_total_delta['Delta'], errors='coerce').fillna(0).astype(int)
    fig_delta.add_trace(go.Scatter(
        x=df_total_delta['Month-Year'],
        y=df_total_delta['Delta'],
        mode='text',
        text=df_total_delta['Delta'].apply(lambda x: f'{int(x):+}'),
        textposition='top center',
        textfont=dict(size=12, color='black'),
        showlegend=False,
        hoverinfo='none'
    ))

    fig_delta.update_layout(
        barmode='stack',
        xaxis={'categoryorder':'array', 'categoryarray': all_months},
        margin=dict(t=50, b=20, l=20, r=20),
        height=450,
        hovermode='x unified',
        dragmode=dragmode,
        title={
            'text': "3. Delta (Actual − Plan) theo Stage",
            'x': 0.5,
            'xanchor': 'center'
        }
    )
    fig_stage_breakdown = px.bar(
        df_stage_summary,
        x='Month-Year', y='Count',
        color='Stage', 
        pattern_shape='Type', # Pattern theo Type (Plan/Actual)
        barmode='group', # Group Plan/Actual cạnh nhau
        text='Count',
        hover_data=['Stage', 'Count', 'Type'],
        color_discrete_map=STAGE_COLOR_MAP
    )
    
    fig_stage_breakdown.update_traces(texttemplate='%{text}', textposition='outside', selector=dict(type='bar'))
    fig_stage_breakdown.update_layout(
        xaxis={'categoryorder':'array', 'categoryarray': all_months}, 
        margin=dict(t=50, b=20, l=20, r=20),
        legend_title_text="Stage (Màu) | Loại (Pattern)",
        height=450,
        hovermode='x unified',
        dragmode=dragmode,
        title={
            'text': "2. Timeline: So sánh Plan vs Actual theo Giai đoạn",
            'x': 0.5,
            'xanchor': 'center'
        }
    )


    # (Removed duplicate delta block: Delta chart already built earlier with per-stage labels and total)

    # 4. Biểu đồ Gantt chart 
    fig_g = go.Figure()
    
    unique_doc_nos = sorted(df_g_filtered['Doc No'].unique(), reverse=True) 

    custom_hovertemplate = "<b>%{y}</b><br>Tên Tài liệu: %{customdata[0]}<br>Discipline: %{customdata[1]}<br>Stage: %{customdata[2]}<br>Loại: %{customdata[3]}<br>Ngày: %{x|%Y-%m-%d}<extra></extra>"

    # Guard: if dataframe is empty, return empty gantt figure and empty tables
    if df_g_filtered.empty:
        # Must return all 6 outputs: 4 figures + detailed-table children + weekly-table children
        return fig_discipline, fig_stage_breakdown, fig_delta, fig_g, [], []

    stages_unique = list(df_g_filtered['Stage'].unique())
    symbol_map = {'Plan': 'circle', 'Actual': 'diamond'}

    for i, stage in enumerate(stages_unique):
        df_stage = df_g_filtered[df_g_filtered['Stage'] == stage].copy()
        # fallback to palette by index when stage not in map
        stage_color = STAGE_COLOR_MAP.get(stage, qualitative_palette[i % len(qualitative_palette)])

        for doc_type in df_stage['Type'].unique():
            df_plot = df_stage[df_stage['Type'] == doc_type].copy()
            # Sanitize text fields (handle None/NaN, bytes, dicts, lists) to safe strings
            def _safe_val(v):
                try:
                    if pd.isna(v):
                        return ''
                except Exception:
                    # pd.isna may fail for some types; fall through
                    pass
                if isinstance(v, (bytes, bytearray)):
                    try:
                        return v.decode('utf-8', errors='replace')
                    except Exception:
                        return repr(v)
                # For containers and other non-primitive types, convert to JSON-like str safely
                if isinstance(v, (dict, list, tuple, set)):
                    try:
                        return str(v)
                    except Exception:
                        return repr(v)
                return str(v)

            for _col in ['Doc Name', 'Discipline', 'Stage', 'Type', 'Doc No']:
                if _col in df_plot.columns:
                    df_plot[_col] = df_plot[_col].apply(_safe_val)

            df_plot['custom_data'] = list(zip(df_plot['Doc Name'], df_plot['Discipline'], df_plot['Stage'], df_plot['Type']))

            fig_g.add_trace(go.Scatter(
                x=df_plot['Date'],
                y=df_plot['Doc No'],
                mode='markers',
                marker=dict(symbol=symbol_map.get(doc_type, 'circle'), 
                            size=7, 
                            color=stage_color, 
                            line=dict(width=1, color=stage_color) 
                           ), 
                name=f'{stage} ({doc_type})', 
                customdata=df_plot['custom_data'],
                hovertemplate=custom_hovertemplate
            ))
            
    fig_g.update_layout(yaxis={'categoryorder': 'array', 
                                'categoryarray': unique_doc_nos, 
                                'dtick': 1 
                               })
             
    fig_g.update_layout(
        xaxis_title="Ngày issue",
        yaxis_title="Doc No",
        height=800,
        legend_title_text='Stage (Type)',
        hovermode="closest",
        margin=dict(l=200, t=50, b=20, r=20),
        dragmode=dragmode,
        title={
            'text': "4. Gantt Chart: Các mốc Plan/Actual theo Stage",
            'x': 0.5,
            'xanchor': 'center'
        }
    )

    # Create detailed summary table for issued documents
    # First for Disciplines
    df_discipline_table = df_sum_filtered.pivot_table(
        values='Count',
        index='Discipline',
        columns=['Month-Year', 'Type'],
        fill_value=0,
        aggfunc='sum'
    ).reset_index()
    
    # Then for Stages
    df_stage_table = df_sum_filtered.pivot_table(
        values='Count',
        index='Stage',
        columns=['Month-Year', 'Type'],
        fill_value=0,
        aggfunc='sum'
    ).reset_index()
    
    # Combine tables with a header row for Disciplines and another for Stages
    table_data = []
    
    # Add Discipline section
    table_data.append({'Category': 'DISCIPLINES', 'Type': ''})  # Header row
    for _, row in df_discipline_table.iterrows():
        discipline_data = {'Category': row['Discipline'], 'Type': ''}
        for month in all_months:
            plan_col = (month, 'Plan')
            actual_col = (month, 'Actual')
            # safe extraction with fill
            try:
                pv = int(row[plan_col]) if plan_col in row and pd.notnull(row[plan_col]) else 0
            except Exception:
                pv = 0
            try:
                av = int(row[actual_col]) if actual_col in row and pd.notnull(row[actual_col]) else 0
            except Exception:
                av = 0
            discipline_data[f'{month} P'] = pv
            discipline_data[f'{month} A'] = av
        table_data.append(discipline_data)
    
    # Add a blank row as separator
    table_data.append({'Category': '', 'Type': ''})
    
    # Add Stage section
    table_data.append({'Category': 'STAGES', 'Type': ''})  # Header row
    for _, row in df_stage_table.iterrows():
        stage_data = {'Category': row['Stage'], 'Type': ''}
        for month in all_months:
            plan_col = (month, 'Plan')
            actual_col = (month, 'Actual')
            try:
                pv = int(row[plan_col]) if plan_col in row and pd.notnull(row[plan_col]) else 0
            except Exception:
                pv = 0
            try:
                av = int(row[actual_col]) if actual_col in row and pd.notnull(row[actual_col]) else 0
            except Exception:
                av = 0
            stage_data[f'{month} P'] = pv
            stage_data[f'{month} A'] = av
        table_data.append(stage_data)

    # Create the table with conditional formatting
    # Build columns: for each month, add a Plan and Actual column with clear headers
    cols = [{'name': ['', 'Category'], 'id': 'Category'}, {'name': ['', 'Type'], 'id': 'Type'}]
    for month in all_months:
        cols.append({'name': [month, 'Plan'], 'id': f'{month} P', 'type': 'numeric'})
        cols.append({'name': [month, 'Actual'], 'id': f'{month} A', 'type': 'numeric'})

    table = dash_table.DataTable(
        data=table_data,
        columns=cols,
        style_table={
            'overflowX': 'auto',
            'width': '100%',
            'minWidth': f'{max(1200, 120 + 90*len(all_months))}px'
        },
        style_cell={
            'textAlign': 'center',
            'padding': '8px',
            'minWidth': '45px',
            'width': '45px',
            'maxWidth': '45px',
            'whiteSpace': 'normal',
            'font-size': '13px'
        },
        style_header={
            'backgroundColor': '#f3f4f6',
            'fontWeight': 'bold',
            'textAlign': 'center',
            'height': 'auto',
            'whiteSpace': 'normal',
            'minWidth': '45px',
            'width': '45px',
            'maxWidth': '45px'
        },
        style_cell_conditional=[
            {
                'if': {'column_id': 'Category'},
                'textAlign': 'left',
                'minWidth': '120px',
                'width': '120px',
                'maxWidth': '120px'
            },
            {
                'if': {'column_id': 'Type'},
                'minWidth': '30px',
                'width': '30px',
                'maxWidth': '30px'
            }
        ],
        style_data_conditional=[
            {
                'if': {'row_index': [0, len(df_discipline_table.index) + 2]},  # Header rows
                'backgroundColor': '#e5e7eb',
                'fontWeight': 'bold'
            },
            {
                'if': {'row_index': len(df_discipline_table.index) + 1},  # Separator row
                'backgroundColor': '#ffffff',
                'height': '20px'
            }
        ],
        merge_duplicate_headers=True,
        fixed_columns={'headers': True, 'data': 1}
    )
    
    # --- Weekly Table ---
    # Add a header and container for the weekly table
    weekly_table_header = html.H3("6. Detailed Weekly Summary Table", style={'marginTop':'40px', 'color': '#1f2937'})
    weekly_table_desc = html.P("Bảng chi tiết số lượng Plan/Actual theo từng tuần", style={'color':'#6b7280'})
    
    # --- Prepare merged dataframe for weekly table ---
    df_merged = pd.read_json(StringIO(data_json['summary']), orient='split')
    # If 'Date' not present, fallback to df_g (which usually has Date)
    if 'Date' not in df_merged.columns and 'Date' in df_g.columns:
        df_merged = df_g.copy()

    # Ensure we have a datetime Date column (may be NaT)
    df_merged['Date'] = pd.to_datetime(df_merged.get('Date', pd.NaT), errors='coerce')

    # If dates are month-level (e.g., day==1 for all rows) then synthesize 4 weeks per month
    month_level = False
    try:
        if df_merged['Date'].notna().any():
            days = df_merged.loc[df_merged['Date'].notna(), 'Date'].dt.day.unique()
            if len(days) == 1 and int(days[0]) in (1, 28, 29, 30):
                # most likely month-level snapshots (commonly day==1)
                month_level = True
        else:
            # no valid dates at all -> treat as month-level if Month-Year exists
            month_level = 'Month-Year' in df_merged.columns
    except Exception:
        month_level = False

    if month_level:
        # Build synthetic weeks: 4 weeks per Month-Year
        if 'Month-Year' not in df_merged.columns:
            df_merged['Month-Year'] = df_merged['Date'].dt.strftime('%Y-%m')
        all_months = sorted(df_merged['Month-Year'].dropna().unique())
        synthetic_weeks = []
        for m in all_months:
            for w in range(1, 5):
                synthetic_weeks.append(f"{m} W{w}")
        # assign all records to Week 1 of their month by default
        df_merged['Week-Year'] = df_merged['Month-Year'].astype(str) + ' W1'
        all_weeks = synthetic_weeks
    else:
        df_merged['Week-Year'] = df_merged['Date'].dt.strftime('%Y-%U')
        all_weeks = sorted(df_merged['Week-Year'].dropna().unique())
    # Disciplines by week
    df_discipline_week = df_merged.groupby(['Week-Year','Discipline','Type']).size().reset_index(name='Count')
    df_discipline_week_pivot = df_discipline_week.pivot_table(
        values='Count',
        index='Discipline',
        columns=['Week-Year', 'Type'],
        fill_value=0,
        aggfunc='sum'
    ).reset_index()
    # Stages by week
    df_stage_week = df_merged.groupby(['Week-Year','Stage','Type']).size().reset_index(name='Count')
    df_stage_week_pivot = df_stage_week.pivot_table(
        values='Count',
        index='Stage',
        columns=['Week-Year', 'Type'],
        fill_value=0,
        aggfunc='sum'
    ).reset_index()
    # Build table data
    week_table_data = []
    week_table_data.append({'Category': 'DISCIPLINES', 'Type': ''})
    for _, row in df_discipline_week_pivot.iterrows():
        discipline_data = {'Category': row['Discipline'], 'Type': ''}
        for week in all_weeks:
            plan_col = (week, 'Plan')
            actual_col = (week, 'Actual')
            try:
                pv = int(row[plan_col]) if plan_col in row and pd.notnull(row[plan_col]) else 0
            except Exception:
                pv = 0
            try:
                av = int(row[actual_col]) if actual_col in row and pd.notnull(row[actual_col]) else 0
            except Exception:
                av = 0
            discipline_data[f'{week} P'] = pv
            discipline_data[f'{week} A'] = av
        week_table_data.append(discipline_data)
    week_table_data.append({'Category': '', 'Type': ''})
    week_table_data.append({'Category': 'STAGES', 'Type': ''})
    for _, row in df_stage_week_pivot.iterrows():
        stage_data = {'Category': row['Stage'], 'Type': ''}
        for week in all_weeks:
            plan_col = (week, 'Plan')
            actual_col = (week, 'Actual')
            try:
                pv = int(row[plan_col]) if plan_col in row and pd.notnull(row[plan_col]) else 0
            except Exception:
                pv = 0
            try:
                av = int(row[actual_col]) if actual_col in row and pd.notnull(row[actual_col]) else 0
            except Exception:
                av = 0
            stage_data[f'{week} P'] = pv
            stage_data[f'{week} A'] = av
        week_table_data.append(stage_data)
    # Build columns
    week_cols = [{'name': ['', 'Category'], 'id': 'Category'}, {'name': ['', 'Type'], 'id': 'Type'}]
    for week in all_weeks:
        week_cols.append({'name': [week, 'Plan'], 'id': f'{week} P', 'type': 'numeric'})
        week_cols.append({'name': [week, 'Actual'], 'id': f'{week} A', 'type': 'numeric'})
    week_table = dash_table.DataTable(
        data=week_table_data,
        columns=week_cols,
        style_table={
            'overflowX': 'auto',
            'width': '100%',
            'minWidth': f'{max(1200, 120 + 90*len(all_weeks))}px'
        },
        style_cell={
            'textAlign': 'center',
            'padding': '8px',
            'minWidth': '45px',
            'width': '45px',
            'maxWidth': '45px',
            'whiteSpace': 'normal',
            'font-size': '13px'
        },
        style_header={
            'backgroundColor': '#f3f4f6',
            'fontWeight': 'bold',
            'textAlign': 'center',
            'height': 'auto',
            'whiteSpace': 'normal',
            'minWidth': '45px',
            'width': '45px',
            'maxWidth': '45px'
        },
        style_cell_conditional=[
            {
                'if': {'column_id': 'Category'},
                'textAlign': 'left',
                'minWidth': '120px',
                'width': '120px',
                'maxWidth': '120px'
            },
            {
                'if': {'column_id': 'Type'},
                'minWidth': '30px',
                'width': '30px',
                'maxWidth': '30px'
            }
        ],
        style_data_conditional=[
            {
                'if': {'row_index': [0, len(df_discipline_week_pivot.index) + 2]},  # Header rows
                'backgroundColor': '#e5e7eb',
                'fontWeight': 'bold'
            },
            {
                'if': {'row_index': len(df_discipline_week_pivot.index) + 1},  # Separator row
                'backgroundColor': '#ffffff',
                'height': '20px'
            }
        ],
        merge_duplicate_headers=True,
        fixed_columns={'headers': True, 'data': 1}
    )
    # Return both tables stacked: detailed table (left) and weekly table (right) as two separate children lists
    detailed_children = [table]
    weekly_children = [weekly_table_header, weekly_table_desc, week_table]
    return fig_discipline, fig_stage_breakdown, fig_delta, fig_g, detailed_children, weekly_children

# --- Clientside callbacks for fullscreen (uses browser Fullscreen API) ---
app.clientside_callback(
    "function(n_clicks){ if(!n_clicks) return ''; try{ var el = document.getElementById('discipline-timeline-chart'); var container = el ? (el.parentElement||el) : null; if(!container) return ''; if(container.requestFullscreen) container.requestFullscreen(); else if(container.webkitRequestFullscreen) container.webkitRequestFullscreen(); else if(container.mozRequestFullScreen) container.mozRequestFullScreen(); else if(container.msRequestFullscreen) container.msRequestFullscreen(); }catch(e){console.error(e);} return ''; }",
    Output('fs-dummy-discipline','children'),
    Input('fullscreen-discipline','n_clicks')
)

app.clientside_callback(
    "function(n_clicks){ if(!n_clicks) return ''; try{ var el = document.getElementById('stage-timeline-chart'); var container = el ? (el.parentElement||el) : null; if(!container) return ''; if(container.requestFullscreen) container.requestFullscreen(); else if(container.webkitRequestFullscreen) container.webkitRequestFullscreen(); else if(container.mozRequestFullScreen) container.mozRequestFullScreen(); else if(container.msRequestFullscreen) container.msRequestFullscreen(); }catch(e){console.error(e);} return ''; }",
    Output('fs-dummy-stage','children'),
    Input('fullscreen-stage','n_clicks')
)

app.clientside_callback(
    "function(n_clicks){ if(!n_clicks) return ''; try{ var el = document.getElementById('delta-chart'); var container = el ? (el.parentElement||el) : null; if(!container) return ''; if(container.requestFullscreen) container.requestFullscreen(); else if(container.webkitRequestFullscreen) container.webkitRequestFullscreen(); else if(container.mozRequestFullScreen) container.mozRequestFullScreen(); else if(container.msRequestFullscreen) container.msRequestFullscreen(); }catch(e){console.error(e);} return ''; }",
    Output('fs-dummy-delta','children'),
    Input('fullscreen-delta','n_clicks')
)

app.clientside_callback(
    "function(n_clicks){ if(!n_clicks) return ''; try{ var el = document.getElementById('gantt-chart'); var container = el ? (el.parentElement||el) : null; if(!container) return ''; if(container.requestFullscreen) container.requestFullscreen(); else if(container.webkitRequestFullscreen) container.webkitRequestFullscreen(); else if(container.mozRequestFullScreen) container.mozRequestFullScreen(); else if(container.msRequestFullscreen) container.msRequestFullscreen(); }catch(e){console.error(e);} return ''; }",
    Output('fs-dummy-gantt','children'),
    Input('fullscreen-gantt','n_clicks')
)


if __name__ == '__main__':
    # Lấy PORT từ môi trường (ENV) được cung cấp bởi Render
    port = int(os.environ.get("PORT", 8050))
    # Render yêu cầu lắng nghe trên 0.0.0.0 để có thể truy cập công khai
    host = '0.0.0.0'
    print("Khởi chạy dashboard: http://{host}:{port}")
    app.run(host=host, port=port)
  
