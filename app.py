# Survey Nest_app.py
import streamlit as st
import pandas as pd
import hashlib
import os
import json
import sys
from io import BytesIO
from datetime import datetime

# ----------------------------
# Configuration / Constants
# ----------------------------
st.set_page_config(page_title="Survey Nest", page_icon="📋", layout="wide")

# Get application directory (works for both script and executable)
def get_app_dir():
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        return os.path.dirname(sys.executable)
    else:
        # Running as script
        return os.path.dirname(os.path.abspath(__file__))

APP_DIR = get_app_dir()
USER_FILE = os.path.join(APP_DIR, "kc_users.csv")        # simple credentials store (username, password_hash)
PROJECTS_FILE = os.path.join(APP_DIR, "kc_projects.json")  # store projects and forms persistently while app file exists

# ----------------------------
# Helper: Storage (users/projects)
# ----------------------------
def ensure_user_file():
    if not os.path.exists(USER_FILE):
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(USER_FILE), exist_ok=True)
        pd.DataFrame(columns=["username", "password_hash"]).to_csv(USER_FILE, index=False)

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def add_user(username: str, password: str) -> bool:
    ensure_user_file()
    df = pd.read_csv(USER_FILE)
    if username in df["username"].values:
        return False
    df = pd.concat([df, pd.DataFrame([{"username": username, "password_hash": hash_password(password)}])], ignore_index=True)
    df.to_csv(USER_FILE, index=False)
    return True

def authenticate(username: str, password: str) -> bool:
    ensure_user_file()
    df = pd.read_csv(USER_FILE)
    row = df[df["username"] == username]
    if row.empty:
        return False
    return row["password_hash"].values[0] == hash_password(password)

def load_projects():
    if os.path.exists(PROJECTS_FILE):
        try:
            with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    return {}

def save_projects(data):
    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(PROJECTS_FILE), exist_ok=True)
    with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ----------------------------
# Initialize session state
# ----------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user" not in st.session_state:
    st.session_state.user = None
if "projects" not in st.session_state:
    st.session_state.projects = load_projects()  # structure: {username: {project_id: {...}}}
if "current_project" not in st.session_state:
    st.session_state.current_project = None
if "message" not in st.session_state:
    st.session_state.message = ""

# ----------------------------
# Utility functions: IDs, XLSForm export, excel export
# ----------------------------
def new_id(prefix="p"):
    return f"{prefix}_{int(datetime.utcnow().timestamp()*1000)}"

def create_empty_project(owner, title):
    pid = new_id("proj")
    project = {
        "id": pid,
        "title": title,
        "owner": owner,
        "created_at": datetime.utcnow().isoformat(),
        "form": [],   # list of questions: {"id","name","label","type","choices":[], "required":bool}
        "data": []    # list of submissions (dicts keyed by question "name")
    }
    return pid, project

def export_xlsform_to_bytes(project):
    # Create survey sheet
    survey_data = []
    for q in project["form"]:
        qtype = q["type"]
        if qtype in ["select_one", "select_multiple"]:
            qtype_str = f"{qtype} {q['name']}"
        else:
            qtype_str = qtype
        survey_data.append({
            "type": qtype_str,
            "name": q["name"],
            "label": q["label"],
            "required": "yes" if q.get("required") else ""
        })
    
    # Create choices sheet
    choices_data = []
    for q in project["form"]:
        if q.get("choices"):
            list_name = q["name"]
            for i, ch in enumerate(q["choices"], start=1):
                choices_data.append({
                    "list_name": list_name,
                    "name": f"opt{i}",
                    "label": ch
                })
    
    # Create Excel file using pandas
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine='xlsxwriter') as writer:
        pd.DataFrame(survey_data).to_excel(writer, sheet_name='survey', index=False)
        if choices_data:
            pd.DataFrame(choices_data).to_excel(writer, sheet_name='choices', index=False)
    
    bio.seek(0)
    return bio

def export_data_to_excel_bytes(project):
    df = pd.DataFrame(project["data"])
    bio = BytesIO()
    df.to_excel(bio, index=False, engine='xlsxwriter')
    bio.seek(0)
    return bio

# ----------------------------
# XLSForm Import Function
# ----------------------------
def import_xlsform(file_bytes, project):
    """
    Import questions from an XLSForm Excel file using pandas
    """
    try:
        # Read the Excel file
        xls = pd.ExcelFile(BytesIO(file_bytes))
        
        # Check if survey sheet exists
        if 'survey' not in xls.sheet_names:
            st.error("XLSForm must contain a 'survey' sheet")
            return False
            
        # Read survey sheet
        survey_df = pd.read_excel(xls, sheet_name='survey')
        
        # Check required columns
        required_columns = ['type', 'name', 'label']
        for col in required_columns:
            if col not in survey_df.columns:
                st.error(f"XLSForm must contain '{col}' column in survey sheet")
                return False
        
        # Read choices sheet if it exists
        choices_df = None
        if 'choices' in xls.sheet_names:
            choices_df = pd.read_excel(xls, sheet_name='choices')
        
        # Process each row in survey sheet
        questions = []
        for _, row in survey_df.iterrows():
            if pd.isna(row['type']) or pd.isna(row['name']) or pd.isna(row['label']):
                continue
                
            q_type = str(row['type'])
            q_name = str(row['name'])
            q_label = str(row['label'])
            q_required = row['required'] if 'required' in row and not pd.isna(row['required']) else False
            
            # Handle select question types
            choices = []
            if q_type.startswith(('select_one', 'select_multiple')):
                # Extract list name from type
                list_name = q_type.split(' ', 1)[1] if ' ' in q_type else q_name
                
                # Find choices for this list
                if choices_df is not None and 'list_name' in choices_df.columns:
                    list_choices = choices_df[choices_df['list_name'] == list_name]
                    if not list_choices.empty:
                        for _, choice_row in list_choices.iterrows():
                            if 'label' in choice_row and not pd.isna(choice_row['label']):
                                choices.append(str(choice_row['label']))
                            elif 'name' in choice_row and not pd.isna(choice_row['name']):
                                choices.append(str(choice_row['name']))
            
            # Normalize question type
            if q_type.startswith('select_one'):
                normalized_type = 'select_one'
            elif q_type.startswith('select_multiple'):
                normalized_type = 'select_multiple'
            else:
                normalized_type = q_type
            
            # Create question object
            question = {
                "id": new_id("q"),
                "name": q_name,
                "label": q_label,
                "type": normalized_type,
                "choices": choices,
                "required": bool(q_required and str(q_required).lower() in ['yes', 'true', '1', 'required'])
            }
            
            questions.append(question)
        
        # Add questions to project
        project["form"].extend(questions)
        return True
        
    except Exception as e:
        st.error(f"Error importing XLSForm: {str(e)}")
        return False

# ----------------------------
# Auth UI
# ----------------------------
def show_auth_sidebar():
    st.sidebar.title("Account")
    choice = st.sidebar.radio("Select", ["Login", "Sign Up", "Help"])
    if choice == "Help":
        st.sidebar.markdown("This App stores users in a local CSV and projects in a JSON file in the app folder.")
        st.sidebar.markdown("Sign Up to create projects, then go to Dashboard.")
        st.sidebar.markdown(f"**Data location:** {APP_DIR}")
        return
    if choice == "Sign Up":
        st.sidebar.subheader("Create account")
        su_user = st.sidebar.text_input("Username", key="su_user")
        su_pass = st.sidebar.text_input("Password", type="password", key="su_pass")
        su_pass2 = st.sidebar.text_input("Confirm Password", type="password", key="su_pass2")
        if st.sidebar.button("Create Account"):
            if not su_user or not su_pass:
                st.sidebar.error("Provide username and password.")
            elif su_pass != su_pass2:
                st.sidebar.error("Passwords do not match.")
            else:
                ok = add_user(su_user, su_pass)
                if ok:
                    st.sidebar.success("Account created. Please Login.")
                else:
                    st.sidebar.warning("Username already exists.")
    elif choice == "Login":
        st.sidebar.subheader("Log in")
        li_user = st.sidebar.text_input("Username", key="li_user")
        li_pass = st.sidebar.text_input("Password", type="password", key="li_pass")
        if st.sidebar.button("Log in"):
            if authenticate(li_user, li_pass):
                st.session_state.logged_in = True
                st.session_state.user = li_user
                st.sidebar.success(f"Logged in as {li_user}")
                # ensure user has entry in projects store
                if li_user not in st.session_state.projects:
                    st.session_state.projects[li_user] = {}
                    save_projects(st.session_state.projects)
            else:
                st.sidebar.error("Username or password incorrect.")

# ----------------------------
# Main App UI
# ----------------------------
st.title("📋 Survey Nest — Form Builder & Data Collector ")
st.markdown("Survey Nest is a free and open-source suite of tools for data collection, management, and analysis.")
st.markdown("Developed for humanitarian, research, and field survey projects.")
st.markdown("Supports offline data collection and mobile use, making it ideal for challenging environments.")

show_auth_sidebar()

if not st.session_state.logged_in:
    st.info("Please Log in or Sign Up from the left sidebar to start.")
    st.stop()

# ensure project container exists
user = st.session_state.user
if user not in st.session_state.projects:
    st.session_state.projects[user] = {}
    save_projects(st.session_state.projects)

# Left: project list / creation
col_left, col_right = st.columns([1.2, 3.8])
with col_left:
    st.header("Your Projects")
    projects = st.session_state.projects.get(user, {})
    if projects:
        # show simple list and selectbox
        proj_items = [(p["title"], pid) for pid, p in projects.items()]
        proj_items.sort()
        titles = [t for t, pid in proj_items]
        sel_title = st.selectbox("Open project", titles, key="proj_select")
        # find pid
        sel_pid = None
        for t, pid in proj_items:
            if t == sel_title:
                sel_pid = pid
                break
        if sel_pid:
            if st.button("Open", key="open_proj"):
                st.session_state.current_project = sel_pid
    else:
        st.write("No projects yet.")

    st.markdown("---")
    st.subheader("Create Project")
    new_title = st.text_input("Project title", key="new_proj_title")
    if st.button("Create Project"):
        if not new_title.strip():
            st.warning("Please provide a project title.")
        else:
            pid, project = create_empty_project(user, new_title.strip())
            st.session_state.projects[user][pid] = project
            save_projects(st.session_state.projects)
            st.success(f"Project '{new_title.strip()}' created.")
            st.session_state.current_project = pid

    st.markdown("---")
    if st.session_state.current_project:
        if st.button("Delete Current Project"):
            pid = st.session_state.current_project
            title = st.session_state.projects[user][pid]["title"]
            del st.session_state.projects[user][pid]
            save_projects(st.session_state.projects)
            st.session_state.current_project = None
            st.success(f"Project '{title}' deleted.")

    if st.button("Logout"):
        st.session_state.logged_in = False
        st.session_state.user = None
        st.session_state.current_project = None
        st.rerun()

# Right: project workspace
with col_right:
    if not st.session_state.current_project:
        st.header("Welcome")
        st.write("Create a project on the left or open an existing one. Once a project is open you can design the form, collect data, and export results.")
    else:
        pid = st.session_state.current_project
        project = st.session_state.projects[user][pid]

        st.header(f"Project: {project['title']}")
        st.markdown(f"**Owner:** {project['owner']} — **Created:** {project['created_at']}")

        # Using tabs individually for richer UI
        t1, t2, t3, t4, t5 = st.tabs(["Form Designer", "Collect (Simulate)", "Data", "Export", "Settings"])

        # ----------------------------
        # FORM DESIGNER
        # ----------------------------
        with t1:
            st.subheader("Form Designer")
            st.markdown("Add questions to your form. Supported types: text, integer, decimal, date, select_one, select_multiple, note.")
            
            # Add XLSForm import section
            st.markdown("### Import from XLSForm")
            uploaded_file = st.file_uploader("Upload XLSForm (.xlsx)", type=["xlsx"], key="xls_upload")
            if uploaded_file is not None:
                if st.button("Import Questions from XLSForm"):
                    success = import_xlsform(uploaded_file.getvalue(), project)
                    if success:
                        st.session_state.projects[user][pid] = project
                        save_projects(st.session_state.projects)
                        st.success("Questions imported successfully!")
                        st.rerun()
            
            # List current questions
            if project["form"]:
                dfq = pd.DataFrame([{"#": i+1, "name": q["name"], "label": q["label"], "type": q["type"], "choices": ", ".join(q.get("choices", []))} for i, q in enumerate(project["form"])])
                st.dataframe(dfq, use_container_width=True)
                # reorder up/down
                cols = st.columns((1,1,1,1))
                with cols[0]:
                    idx_up = st.number_input("Move up (question #)", min_value=1, max_value=len(project["form"]), step=1, key="move_up_idx")
                    if st.button("Move Up"):
                        i = idx_up-1
                        if i > 0:
                            project["form"][i-1], project["form"][i] = project["form"][i], project["form"][i-1]
                            st.session_state.projects[user][pid] = project
                            save_projects(st.session_state.projects)
                            st.rerun()
                with cols[1]:
                    idx_down = st.number_input("Move down (question #)", min_value=1, max_value=len(project["form"]), step=1, key="move_down_idx")
                    if st.button("Move Down"):
                        i = idx_down-1
                        if i < len(project["form"])-1:
                            project["form"][i+1], project["form"][i] = project["form"][i], project["form"][i+1]
                            st.session_state.projects[user][pid] = project
                            save_projects(st.session_state.projects)
                            st.rerun()
                with cols[2]:
                    del_idx = st.number_input("Delete (question #)", min_value=1, max_value=len(project["form"]), step=1, key="del_idx")
                    if st.button("Delete Question"):
                        removed = project["form"].pop(del_idx-1)
                        st.session_state.projects[user][pid] = project
                        save_projects(st.session_state.projects)
                        st.success(f"Deleted question: {removed['label']}")
                        st.rerun()
                with cols[3]:
                    if st.button("Clear all questions"):
                        project["form"] = []
                        project["data"] = []  # reset collected data
                        st.session_state.projects[user][pid] = project
                        save_projects(st.session_state.projects)
                        st.success("Cleared form and data.")
                        st.rerun()
            else:
                st.info("No questions yet. Add questions with the form below.")

            st.markdown("### Add / Edit Question")
            with st.form("q_form", clear_on_submit=False):
                q_label = st.text_input("Question label (what user sees)", key="q_label")
                q_type = st.selectbox("Question type", ["text", "integer", "decimal", "date", "select_one", "select_multiple", "note"], key="q_type")
                q_required = st.checkbox("Required", key="q_required")
                q_choices = ""
                if q_type in ["select_one", "select_multiple"]:
                    q_choices = st.text_area("Choices (one per line)", placeholder="Option 1\nOption 2\nOption 3", key="q_choices")
                # Optional: custom name
                q_name = st.text_input("Variable name (optional, auto-generated if empty)", key="q_name")
                submitted = st.form_submit_button("Add Question")
                if submitted:
                    if not q_label.strip():
                        st.error("Question label is required.")
                    else:
                        # generate variable name if empty: sanitize label
                        if not q_name.strip():
                            base = "".join(ch if ch.isalnum() else "_" for ch in q_label.strip()).lower()
                            # ensure uniqueness
                            existing_names = {q["name"] for q in project["form"]}
                            candidate = base or f"q{len(project['form'])+1}"
                            suffix = 1
                            while candidate in existing_names:
                                candidate = f"{base}_{suffix}"
                                suffix += 1
                            q_name = candidate
                        else:
                            q_name = q_name.strip()
                        question = {
                            "id": new_id("q"),
                            "name": q_name,
                            "label": q_label.strip(),
                            "type": q_type,
                            "choices": [c.strip() for c in q_choices.splitlines() if c.strip()] if q_choices else [],
                            "required": bool(q_required)
                        }
                        project["form"].append(question)
                        st.session_state.projects[user][pid] = project
                        save_projects(st.session_state.projects)
                        st.success(f"Added question: {q_label.strip()}")

        # ----------------------------
        # COLLECT (simulate)
        # ----------------------------
        with t2:
            st.subheader("Collect (Simulate Data Entry)")
            st.markdown("Use this interface to simulate filling out the form and creating submissions (useful for testing).")
            if not project["form"]:
                st.info("Add questions first in Form Designer.")
            else:
                with st.form("collect_form"):
                    entry = {}
                    for q in project["form"]:
                        qn = q["name"]
                        qlabel = q["label"]
                        qtype = q["type"]
                        if qtype == "text":
                            entry[qn] = st.text_input(qlabel, key=f"c_{qn}")
                        elif qtype == "integer":
                            entry[qn] = st.number_input(qlabel, step=1.0, format="%d", key=f"c_{qn}")
                        elif qtype == "decimal":
                            entry[qn] = st.number_input(qlabel, step=0.01, key=f"c_{qn}")
                        elif qtype == "date":
                            entry[qn] = st.date_input(qlabel, key=f"c_{qn}")
                        elif qtype == "note":
                            st.markdown(f"**{qlabel}**")
                            entry[qn] = None
                        elif qtype in ["select_one", "select_multiple"]:
                            if qtype == "select_one":
                                entry[qn] = st.selectbox(qlabel, options=[""] + q.get("choices", []), key=f"c_{qn}")
                            else:
                                entry[qn] = st.multiselect(qlabel, options=q.get("choices", []), key=f"c_{qn}")
                    if st.form_submit_button("Submit record"):
                        # simple validation for required fields
                        missing = []
                        for q in project["form"]:
                            if q.get("required") and (entry.get(q["name"]) in (None, "", [], 0)):
                                missing.append(q["label"])
                        if missing:
                            st.error(f"Please fill required questions: {', '.join(missing)}")
                        else:
                            # normalize date values
                            for k, v in entry.items():
                                if isinstance(v, (pd.Timestamp, datetime)):
                                    entry[k] = str(v)
                            entry_meta = {"_submission_time": datetime.utcnow().isoformat()}
                            entry_meta.update(entry)
                            project["data"].append(entry_meta)
                            st.session_state.projects[user][pid] = project
                            save_projects(st.session_state.projects)
                            st.success("Record submitted (simulated).")

        # ----------------------------
        # DATA
        # ----------------------------
        with t3:
            st.subheader("Data Viewer")
            if not project["data"]:
                st.info("No data submitted yet.")
            else:
                df = pd.DataFrame(project["data"])
                st.dataframe(df, use_container_width=True)
                # quick filters
                cols = st.multiselect("Columns to show", options=list(df.columns), default=list(df.columns))
                st.download_button("Download CSV", df.to_csv(index=False).encode("utf-8"), file_name=f"{project['title']}_data.csv", mime="text/csv")
                excel_bytes = export_data_to_excel_bytes(project)
                st.download_button("Download Excel", excel_bytes, file_name=f"{project['title']}_data.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

                # delete individual row
                st.markdown("**Delete submission**")
                idx_to_delete = st.number_input("Submission # to delete (1-based)", min_value=1, max_value=len(project["data"]), step=1, key="del_sub_idx")
                if st.button("Delete Submission"):
                    removed = project["data"].pop(idx_to_delete-1)
                    st.session_state.projects[user][pid] = project
                    save_projects(st.session_state.projects)
                    st.success(f"Deleted submission #{idx_to_delete}.")

        # ----------------------------
        # EXPORT
        # ----------------------------
        with t4:
            st.subheader("Export")
            st.markdown("Export form as XLSForm (survey + choices) or download collected data.")
            if st.button("Export XLSForm (download)"):
                xls_bytes = export_xlsform_to_bytes(project)
                st.download_button("Download XLSForm", xls_bytes, file_name=f"{project['title']}_xlsform.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            st.write("Tip: open the XLSForm in Excel or KoboToolbox 'Import XLSForm' to deploy it.")

        # ----------------------------
        # SETTINGS
        # ----------------------------
        with t5:
            st.subheader("Settings")
            st.write("Project metadata & simple settings.")
            new_title = st.text_input("Rename project", value=project["title"])
            if st.button("Rename project"):
                project["title"] = new_title.strip() or project["title"]
                st.session_state.projects[user][pid] = project
                save_projects(st.session_state.projects)
                st.success("Project renamed.")
            st.write("Owner:", project["owner"])
            st.write("Created:", project["created_at"])

        # save back any changes
        st.session_state.projects[user][pid] = project
        save_projects(st.session_state.projects)
