import html
import json
import os
from datetime import date
from dotenv import load_dotenv
from flask import Flask, render_template_string, request
from google import genai
from google.cloud import bigquery
from google.genai import types
import pypdf

load_dotenv()

app = Flask(__name__)

# -------------------------------------------------------------------------
# 1. Initialize Clients (Runs once when server starts)
# -------------------------------------------------------------------------
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
DATASET_ID = os.getenv("BQ_DATASET", "retail_dataset")

ai_client = genai.Client()  # Picks up GEMINI_API_KEY from .env
bq_client = bigquery.Client(project=PROJECT_ID)

# -------------------------------------------------------------------------
# 2. Extract System Instructions from PDF (Cached on startup)
# -------------------------------------------------------------------------
pdf_path = "Retail AI Agent - Context File for Agent.pdf"
system_instruction_text = ""

if os.path.exists(pdf_path):
    reader = pypdf.PdfReader(pdf_path)
    system_instruction_text = "\n".join(
        [page.extract_text() for page in reader.pages if page.extract_text()]
    )
else:
    print(f"Warning: '{pdf_path}' not found. System instructions will be empty.")

# -------------------------------------------------------------------------
# 3. Extract Daily Metrics (In-Memory Cache)
# -------------------------------------------------------------------------
daily_metrics_cache = {
    "date": None,
    "data": {}
}


def get_daily_metrics():
    today = date.today()

    if daily_metrics_cache["date"] == today and daily_metrics_cache["data"]:
        return daily_metrics_cache["data"]

    sql_query = f"""
    DECLARE DT DATE;
    SET DT = (SELECT MAX(trans_dt) FROM `{PROJECT_ID}.{DATASET_ID}.rtl_agg_sales_kpis_daily`);

    SELECT 
        CAST(trans_dt AS STRING) AS trans_dt, 
        transactions, 
        gross_sales, 
        gross_sales_per_transaction, 
        average_basket_size
    FROM `{PROJECT_ID}.{DATASET_ID}.rtl_agg_sales_kpis_daily`
    WHERE trans_dt = DT;
    """

    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=50 * 1024 * 1024  # 50MB Scan Cap limit
    )

    try:
        query_job = bq_client.query(sql_query, job_config=job_config)
        results = [dict(row) for row in query_job.result()]

        if results:
            daily_metrics_cache["data"] = results[0]
            daily_metrics_cache["date"] = today
        else:
            daily_metrics_cache["data"] = {
                "trans_dt": "N/A",
                "transactions": 0,
                "gross_sales": 0,
                "gross_sales_per_transaction": 0,
                "average_basket_size": 0,
            }
            daily_metrics_cache["date"] = today

    except Exception as e:
        print(f"Failed to fetch daily metrics: {e}")
        return {
            "trans_dt": "Error",
            "transactions": 0,
            "gross_sales": 0,
            "gross_sales_per_transaction": 0,
            "average_basket_size": 0,
        }

    return daily_metrics_cache["data"]


# -------------------------------------------------------------------------
# 4. Embedded HTML Template with Chart.js
# -------------------------------------------------------------------------
HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Retail AI Agent</title>
    <!-- Chart.js CDN for dynamic visual analytics -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 800px;
            margin: 40px auto;
            padding: 0 20px;
            color: #1e293b;
            line-height: 1.5;
        }
        .header-container {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 12px;
        }
        h2 { 
            color: #0f172a; 
            margin: 0 0 6px 0; 
        }
        .header-date {
            font-size: 14px;
            font-weight: 600;
            color: #475569;
            background-color: #f1f5f9;
            padding: 6px 12px;
            border-radius: 6px;
            border: 1px solid #e2e8f0;
            white-space: nowrap;
        }
        .metrics-banner {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 12px;
            background-color: #0f172a;
            color: #ffffff;
            padding: 16px 20px;
            border-radius: 8px;
            margin: 16px 0 24px 0;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }
        .metric-item {
            text-align: center;
        }
        .metric-label {
            font-size: 12px;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .metric-value {
            font-weight: 700;
            font-size: 17px;
            margin-top: 4px;
            color: #ffffff;
        }
        textarea {
            width: 100%;
            height: 100px;
            padding: 10px;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            box-sizing: border-box;
            font-size: 14px;
        }
        button {
            margin-top: 12px;
            background-color: #2563eb;
            color: white;
            border: none;
            padding: 10px 20px;
            font-size: 14px;
            border-radius: 6px;
            cursor: pointer;
        }
        button:hover { 
            background-color: #1d4ed8; 
        }
        .chart-box {
            margin-top: 24px;
            padding: 18px;
            background-color: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
        }
        .output-box {
            margin-top: 24px;
            padding: 18px;
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            white-space: pre-wrap;
        }
        .error-box {
            margin-top: 24px;
            padding: 18px;
            background-color: #fef2f2;
            border: 1px solid #fecaca;
            color: #991b1b;
            border-radius: 6px;
            white-space: pre-wrap;
        }
    </style>
</head>
<body>
    <div class="header-container">
        <div>
            <h2>Retail AI Agent</h2>
            <p style="color: #64748b; margin: 0;">Ask questions about retail data and get automated SQL insights.</p>
        </div>
        <div class="header-date">
            Date: {{ metrics.trans_dt or 'N/A' }}
        </div>
    </div>

    <div class="metrics-banner">
        <div class="metric-item">
            <div class="metric-label">Gross Revenue</div>
            <div class="metric-value">${{ "{:,.2f}".format(metrics.gross_sales or 0) }}</div>
        </div>
        <div class="metric-item">
            <div class="metric-label">Transactions</div>
            <div class="metric-value">{{ "{:,}".format(metrics.transactions or 0) }}</div>
        </div>
        <div class="metric-item">
            <div class="metric-label">Avg. Order Value</div>
            <div class="metric-value">${{ "{:,.2f}".format(metrics.gross_sales_per_transaction or 0) }}</div>
        </div>
        <div class="metric-item">
            <div class="metric-label">Avg. Basket Size</div>
            <div class="metric-value">{{ "{:,.2f}".format(metrics.average_basket_size or 0) }}</div>
        </div>
    </div>
    
    <form method="POST" action="/process">
        <textarea name="user_text" placeholder="e.g., What was our highest revenue day?" required>{{ original_text or '' }}</textarea>
        <br>
        <button type="submit">Submit Query</button>
    </form>

    {% if error %}
        <div class="error-box">
            <h3>Execution Error:</h3>
            <div>{{ error }}</div>
        </div>
    {% elif result %}
        {% if chart_data %}
            <div class="chart-box">
                <canvas id="agentChart"></canvas>
            </div>
            <script>
                const ctx = document.getElementById('agentChart').getContext('2d');
                new Chart(ctx, {{ chart_data | tojson }});
            </script>
        {% endif %}

        <div class="output-box">
            <h3>Findings:</h3>
            <div>{{ result }}</div>
        </div>
    {% endif %}
</body>
</html>
"""


# -------------------------------------------------------------------------
# 5. Route Handlers
# -------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def home():
    metrics = get_daily_metrics()
    return render_template_string(HTML_PAGE, metrics=metrics)


@app.route("/process", methods=["POST"])
def process():
    metrics = get_daily_metrics()
    user_question = request.form.get("user_text", "").strip()

    try:
        # Step 1: User Question -> Text-to-SQL
        sql_generation_prompt = f"""
        Write a BigQuery SQL query to answer this business question: "{user_question}".
        
        STRICT RULES:
        1. Target ONLY the dataset `{DATASET_ID}`.
        2. DO NOT query INFORMATION_SCHEMA or system metadata views.
        3. Return ONLY the raw SQL code. Do not wrap it in markdown backticks. Do not include introductory text or follow-up explanations.
        """

        sql_res = ai_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=sql_generation_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction_text,
                temperature=0.0,
            ),
        )

        # Clean SQL string and enforce dynamic project/dataset identifiers
        generated_sql = (
            sql_res.text.replace("```sql", "")
            .replace("```", "")
            .replace("your_project", PROJECT_ID if PROJECT_ID else "")
            .replace("your_dataset", DATASET_ID)
            .strip()
        )

        # Step 2: Execute SQL Query in BigQuery
        dataset_ref = bigquery.DatasetReference(PROJECT_ID, DATASET_ID)
        job_config = bigquery.QueryJobConfig(
            default_dataset=dataset_ref,
            maximum_bytes_billed=100 * 1024 * 1024,  # 100 MB scan cap
        )

        query_job = bq_client.query(generated_sql, job_config=job_config)
        results = [dict(row) for row in query_job.result()]

        # Step 3: Dynamic Chart.js Data Packaging (Multi-row results)
        chart_config = None
        if len(results) > 1:
            keys = list(results[0].keys())
            label_key = keys[0]
            metric_key = keys[1] if len(keys) > 1 else keys[0]

            labels = [str(r[label_key]) for r in results]
            values = [
                float(r[metric_key]) if isinstance(r[metric_key], (int, float)) else r[metric_key]
                for r in results
            ]

            chart_config = {
                "type": "bar",
                "data": {
                    "labels": labels,
                    "datasets": [
                        {
                            "label": metric_key.replace("_", " ").title(),
                            "data": values,
                            "backgroundColor": "#2563eb",
                            "borderColor": "#1d4ed8",
                            "borderWidth": 1,
                        }
                    ],
                },
                "options": {
                    "responsive": True,
                    "scales": {"y": {"beginAtZero": True}},
                },
            }

        # Step 4: AI Synthesis & Strategy Formulation
        synthesis_prompt = f"""
        Business Question: {user_question}
        SQL Query Used: {generated_sql}
        Database Results: {results}

        Please provide:
        1. The direct answer to the question using the database numbers.
        2. Executive context and key takeaways.
        3. 2-3 strategic business recommendations.
        """

        final_insight = ai_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=synthesis_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction_text,
                temperature=0.2,
            ),
        )

        return render_template_string(
            HTML_PAGE,
            metrics=metrics,
            original_text=user_question,
            result=final_insight.text,
            chart_data=chart_config,
        )

    except Exception as e:
        return render_template_string(
            HTML_PAGE,
            metrics=metrics,
            original_text=user_question,
            error=f"{type(e).__name__}: {str(e)}",
            chart_data=None,
        )


if __name__ == "__main__":
    app.run(port=5001, debug=True, use_reloader=False)
