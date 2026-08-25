import html
import os
from flask import Flask, render_template_string, request
from google import genai
from google.cloud import bigquery
from google.genai import types
import pypdf
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# -------------------------------------------------------------------------
# 1. Initialize Clients (Runs once when server starts)
# -------------------------------------------------------------------------
# Ensure GEMINI_API_KEY and GCP_PROJECT_ID are set in your environment
# Now these will automatically find the values from your .env file
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
ai_client = genai.Client()  # Automatically picks up GEMINI_API_KEY from .env
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
    print(
        f"Warning: '{pdf_path}' not found. System instructions will be empty."
    )

# -------------------------------------------------------------------------
# 3. Embedded HTML Template
# -------------------------------------------------------------------------
HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Retail AI Agent</title>
    <style>
        body {
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 700px;
            margin: 40px auto;
            padding: 0 20px;
            color: #1e293b;
            line-height: 1.5;
        }
        h2 { color: #0f172a; margin-bottom: 8px; }
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
        button:hover { background-color: #1d4ed8; }
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
    <h2>Retail AI Agent</h2>
    <p style="color: #64748b; margin-top: 0;">Ask questions about retail data and get automated SQL insights.</p>
    
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
        <div class="output-box">
            <h3>Findings:</h3>
            <div>{{ result }}</div>
        </div>
    {% endif %}
</body>
</html>
"""


@app.route("/", methods=["GET"])
def home():
    return render_template_string(HTML_PAGE)


@app.route("/process", methods=["POST"])
def process():
    user_question = request.form.get("user_text", "").strip()

    try:
        # Step 1: User Question -> Text-to-SQL
        sql_generation_prompt = f"""
        Write a BigQuery SQL query to answer this business question: "{user_question}".
        IMPORTANT: Return ONLY the raw SQL code. Do not wrap it in markdown backticks. Do not include introductory text or follow-up explanations.
        """

        sql_res = ai_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=sql_generation_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction_text,
                temperature=0.0,
            ),
        )

        # Clean SQL string
        generated_sql = (
            sql_res.text.replace("```sql", "")
            .replace("```", "")
            .replace("your_project", os.getenv("GCP_PROJECT_ID", ""))
            .replace("your_dataset", os.getenv("BQ_DATASET", "retail_dataset"))
            .strip()
        )

        # Step 2: Execute SQL Query in BigQuery
        project_id = os.getenv("GCP_PROJECT_ID")
        dataset_id = os.getenv("BQ_DATASET", "retail_dataset")

        dataset_ref = bigquery.DatasetReference(project_id, dataset_id)
        job_config = bigquery.QueryJobConfig(
            default_dataset=dataset_ref,
            maximum_bytes_billed=100 * 1024 * 1024,  # 100 MB scan cap
        )

        query_job = bq_client.query(generated_sql, job_config=job_config)
        results = [dict(row) for row in query_job.result()]

        # Step 3: Synthesis
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
            original_text=user_question,
            result=final_insight.text,
        )

    except Exception as e:
        # Return visible error in browser rather than breaking the Flask server
        return render_template_string(
            HTML_PAGE,
            original_text=user_question,
            error=f"{type(e).__name__}: {str(e)}",
        )


if __name__ == "__main__":
    app.run(port=5001, debug=True, use_reloader=False)
