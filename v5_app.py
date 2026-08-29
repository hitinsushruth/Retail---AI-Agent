import html
import json
import os
from datetime import date
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request
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
# 4. Single-Page Interactive HTML/JS Template
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
        .history-feed {
            display: flex;
            flex-direction: column;
            gap: 24px;
            margin-bottom: 24px;
        }
        .entry-block {
            border-bottom: 2px dashed #e2e8f0;
            padding-bottom: 24px;
        }
        .question-badge-box {
            padding: 14px 18px;
            background-color: #f1f5f9;
            border-left: 4px solid #2563eb;
            border-radius: 4px;
            margin-bottom: 16px;
        }
        .question-badge-box strong {
            color: #1e293b;
            display: block;
            margin-bottom: 4px;
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .question-text {
            color: #334155;
            font-size: 15px;
        }
        .chart-box {
            margin-top: 16px;
            padding: 18px;
            background-color: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
        }
        .output-box {
            margin-top: 16px;
            padding: 18px;
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            white-space: pre-wrap;
        }
        .error-box {
            margin-top: 16px;
            padding: 18px;
            background-color: #fef2f2;
            border: 1px solid #fecaca;
            color: #991b1b;
            border-radius: 6px;
            white-space: pre-wrap;
        }
        .form-section {
            margin-top: 20px;
            padding-top: 20px;
            border-top: 1px solid #e2e8f0;
        }
        textarea {
            width: 100%;
            height: 90px;
            padding: 10px;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            box-sizing: border-box;
            font-size: 14px;
            font-family: inherit;
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
        button:disabled {
            background-color: #94a3b8;
            cursor: not-allowed;
        }
    </style>
</head>
<body>
    <!-- Top Header: Title & Date -->
    <div class="header-container">
        <div>
            <h2>Retail AI Agent</h2>
            <p style="color: #64748b; margin: 0;">Ask questions about retail data and get automated SQL insights.</p>
        </div>
        <div class="header-date">
            Date: {{ metrics.trans_dt or 'N/A' }}
        </div>
    </div>

    <!-- Daily Metrics Banner -->
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

    <!-- Appended Conversation Stream Container -->
    <div id="historyFeed" class="history-feed"></div>

    <!-- Pinned Input Form -->
    <div class="form-section">
        <form id="queryForm">
            <textarea id="userText" name="user_text" placeholder="e.g., What was our highest revenue day?" required></textarea>
            <br>
            <button type="submit" id="submitBtn">Submit Query</button>
        </form>
    </div>

    <script>
        let queryCounter = 0;
        const queryForm = document.getElementById('queryForm');
        const userTextInput = document.getElementById('userText');
        const submitBtn = document.getElementById('submitBtn');
        const historyFeed = document.getElementById('historyFeed');

        queryForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const question = userTextInput.value.trim();
            if (!question) return;

            // Lock UI during execution
            submitBtn.disabled = true;
            submitBtn.textContent = 'Processing...';

            queryCounter++;
            const currentQueryId = queryCounter;

            // Create and append question + loading state immediately
            const entryBlock = document.createElement('div');
            entryBlock.className = 'entry-block';
            entryBlock.id = `entry-${currentQueryId}`;
            entryBlock.innerHTML = `
                <div class="question-badge-box">
                    <strong>Question #${currentQueryId}</strong>
                    <div class="question-text">${escapeHtml(question)}</div>
                </div>
                <div class="output-box" id="loading-${currentQueryId}">Running BigQuery pipeline & synthesizing insights...</div>
            `;
            historyFeed.appendChild(entryBlock);

            // Clear input box
            userTextInput.value = '';

            try {
                const formData = new FormData();
                formData.append('user_text', question);

                const response = await fetch('/process', {
                    method: 'POST',
                    body: formData
                });

                const data = await response.json();
                const loadingElement = document.getElementById(`loading-${currentQueryId}`);

                if (data.error) {
                    loadingElement.outerHTML = `
                        <div class="error-box">
                            <h3 style="margin-top: 0;">Execution Error:</h3>
                            <div>${escapeHtml(data.error)}</div>
                        </div>
                    `;
                } else {
                    let chartHtml = '';
                    if (data.chart_data) {
                        chartHtml = `
                            <div class="chart-box">
                                <canvas id="chart-${currentQueryId}"></canvas>
                            </div>
                        `;
                    }

                    loadingElement.outerHTML = `
                        ${chartHtml}
                        <div class="output-box">
                            <h3 style="margin-top: 0;">Findings:</h3>
                            <div>${escapeHtml(data.result)}</div>
                        </div>
                    `;

                    // Render Chart.js instance if chart_data exists
                    if (data.chart_data) {
                        const ctx = document.getElementById(`chart-${currentQueryId}`).getContext('2d');
                        new Chart(ctx, data.chart_data);
                    }
                }
            } catch (err) {
                const loadingElement = document.getElementById(`loading-${currentQueryId}`);
                loadingElement.outerHTML = `
                    <div class="error-box">
                        <h3 style="margin-top: 0;">Client Error:</h3>
                        <div>${escapeHtml(err.message)}</div>
                    </div>
                `;
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = 'Submit Follow-Up';
                userTextInput.placeholder = 'Ask a follow-up question...';
                // Scroll down smoothly to show new findings
                entryBlock.scrollIntoView({ behavior: 'smooth' });
            }
        });

        function escapeHtml(str) {
            if (!str) return '';
            return str
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }
    </script>
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

        return jsonify({
            "result": final_insight.text,
            "chart_data": chart_config,
            "error": None
        })

    except Exception as e:
        return jsonify({
            "result": None,
            "chart_data": None,
            "error": f"{type(e).__name__}: {str(e)}"
        })


if __name__ == "__main__":
    app.run(port=5001, debug=True, use_reloader=False)
