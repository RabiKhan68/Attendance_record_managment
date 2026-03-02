from flask import Flask, render_template, request, redirect, session, url_for, flash, send_file
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date
from fpdf import FPDF
import tempfile
import os

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "fallback-secret")  # Change in production

# ---------- DATABASE CONNECTION ----------
def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),  # Your MySQL password
        database=os.getenv("DB_NAME"),
        auth_plugin="mysql_native_password"
    )

# ---------- TEACHER SIGNUP ----------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]
        password2 = request.form["password2"]

        if password != password2:
            error = "Passwords do not match!"
        else:
            db = get_db_connection()
            cursor = db.cursor(dictionary=True)
            cursor.execute("SELECT * FROM teachers WHERE email=%s", (email,))
            if cursor.fetchone():
                error = "Email already registered!"
            else:
                hashed_pw = generate_password_hash(password)
                cursor.execute(
                    "INSERT INTO teachers (name, email, password_hash) VALUES (%s,%s,%s)",
                    (name, email, hashed_pw)
                )
                db.commit()
                flash("Signup successful! Please login.", "success")
                cursor.close()
                db.close()
                return redirect(url_for("login"))
            cursor.close()
            db.close()
    return render_template("signup.html", error=error)

# ---------- TEACHER LOGIN ----------
@app.route("/", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM teachers WHERE email=%s", (email,))
        teacher = cursor.fetchone()
        cursor.close()
        db.close()

        if teacher and check_password_hash(teacher["password_hash"], password):
            session["teacher_id"] = teacher["teacher_id"]
            session["teacher_name"] = teacher["name"]
            return redirect(url_for("dashboard"))
        else:
            error = "Invalid email or password"
    return render_template("login.html", error=error)

@app.route("/dashboard")
def dashboard():
    if "teacher_id" not in session:
        return redirect(url_for("login"))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Stats
    cursor.execute("SELECT COUNT(*) AS total_courses FROM teacher_courses WHERE teacher_id=%s", (session["teacher_id"],))
    total_courses = cursor.fetchone()["total_courses"]

    cursor.execute("""
        SELECT COUNT(DISTINCT cs.student_id) AS total_students
        FROM teacher_courses tc
        JOIN course_students cs ON tc.course_id = cs.course_id
        WHERE tc.teacher_id=%s
    """, (session["teacher_id"],))
    total_students = cursor.fetchone()["total_students"]

    cursor.execute("""
        SELECT COUNT(DISTINCT date) AS total_classes
        FROM attendance a
        JOIN teacher_courses tc ON a.course_id = tc.course_id
        WHERE tc.teacher_id=%s
    """, (session["teacher_id"],))
    total_classes = cursor.fetchone()["total_classes"]

    cursor.execute("""
        SELECT 
            SUM(CASE WHEN status='Absent' THEN 1 ELSE 0 END)/COUNT(*)*100 AS avg_absent
        FROM attendance a
        JOIN teacher_courses tc ON a.course_id = tc.course_id
        WHERE tc.teacher_id=%s
    """, (session["teacher_id"],))
    avg_absent = cursor.fetchone()["avg_absent"] or 0

    # Fetch all courses assigned to teacher (needed for attendance links)
    cursor.execute("""
        SELECT c.course_id, c.course_name
        FROM courses c
        JOIN teacher_courses tc ON c.course_id = tc.course_id
        WHERE tc.teacher_id=%s
    """, (session["teacher_id"],))
    courses = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "dashboard.html",
        total_courses=total_courses,
        total_students=total_students,
        total_classes=total_classes,
        avg_absent=round(avg_absent, 2),
        courses=courses  # ✅ pass courses so template can loop
    )

# Teacher view students
@app.route("/teacher/course/<int:course_id>/students")
def teacher_course_students(course_id):
    if "teacher_id" not in session:
        return redirect(url_for("login"))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Get course info
    cursor.execute("SELECT course_name, class_id FROM courses WHERE course_id=%s", (course_id,))
    course = cursor.fetchone()
    if not course:
        cursor.close()
        db.close()
        flash("Course not found", "danger")
        return redirect(url_for("classes"))

    # Get students
    cursor.execute("""
        SELECT s.student_id, s.student_name
        FROM course_students cs
        JOIN students s ON cs.student_id = s.student_id
        WHERE cs.course_id=%s
        ORDER BY s.student_id
    """, (course_id,))
    students = cursor.fetchall()
    cursor.close()
    db.close()

    return render_template(
        "course_students.html",
        students=students,
        course_name=course["course_name"],
        class_id=course["class_id"]
    )

# ---------- ATTENDANCE PDF ----------
@app.route("/attendance/pdf/<int:course_id>")
def attendance_pdf(course_id):
    if "teacher_id" not in session:
        return redirect(url_for("login"))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT course_name FROM courses WHERE course_id=%s", (course_id,))
    course = cursor.fetchone()
    course_name = course["course_name"] if course else "Course"

    cursor.execute("""
        SELECT s.student_name, a.date, a.status
        FROM students s
        JOIN courses c ON s.class_id = c.class_id
        LEFT JOIN attendance a ON a.student_id = s.student_id AND a.course_id=%s
        WHERE c.course_id=%s
        ORDER BY s.student_id, a.date
    """, (course_id, course_id))
    records = cursor.fetchall()
    cursor.close()
    db.close()

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, f"Attendance Report - {course_name}", 0, 1, "C")
    pdf.ln(5)

    pdf.set_font("Arial", "B", 12)
    pdf.cell(60, 10, "Student Name", 1)
    pdf.cell(40, 10, "Date", 1)
    pdf.cell(30, 10, "Status", 1)
    pdf.ln()

    pdf.set_font("Arial", "", 12)
    for row in records:
        pdf.cell(60, 10, row["student_name"], 1)
        pdf.cell(40, 10, str(row["date"]) if row["date"] else "-", 1)
        pdf.cell(30, 10, row["status"] if row["status"] else "-", 1)
        pdf.ln()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        pdf.output(tmp.name)
        tmp_path = tmp.name

    return send_file(tmp_path, download_name=f"{course_name}_attendance.pdf", as_attachment=True)

# ---------- CLASSES ----------
@app.route("/classes")
def classes():
    if "teacher_id" not in session:
        return redirect(url_for("login"))
    
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM classes ORDER BY class_id")
    classes = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template("classes.html", classes=classes)

# ---------- COURSES BY CLASS ----------
@app.route("/courses/<int:class_id>")
def courses_by_class(class_id):
    if "teacher_id" not in session:
        return redirect(url_for("login"))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT c.course_id, c.course_name
        FROM courses c
        JOIN teacher_courses tc ON c.course_id = tc.course_id
        WHERE tc.teacher_id=%s AND c.class_id=%s
    """, (session["teacher_id"], class_id))
    courses = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template("courses.html", courses=courses)

# ---------- ATTENDANCE ----------
@app.route("/attendance/<int:course_id>", methods=["GET", "POST"])
def attendance(course_id):
    if "teacher_id" not in session:
        return redirect(url_for("login"))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT s.student_id, s.student_name
        FROM students s
        JOIN courses c ON s.class_id = c.class_id
        WHERE c.course_id=%s
    """, (course_id,))
    students = cursor.fetchall()

    today_date = date.today()

    if request.method == "POST":
        for student in students:
            status = request.form.get(f"status_{student['student_id']}")
            if status:
                cursor.execute("""
                    SELECT * FROM attendance 
                    WHERE course_id=%s AND student_id=%s AND date=%s
                """, (course_id, student["student_id"], today_date))
                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO attendance (course_id, student_id, status, date)
                        VALUES (%s,%s,%s,%s)
                    """, (course_id, student["student_id"], status, today_date))
        db.commit()
        cursor.close()
        db.close()
        flash("Attendance saved successfully!", "success")
        return redirect(url_for("classes"))

    cursor.close()
    db.close()
    return render_template("attendance.html", students=students, course_id=course_id, today=today_date)

# ---------- ANALYTICS ----------
@app.route("/analytics")
def analytics():
    if "teacher_id" not in session:
        return redirect(url_for("login"))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT c.course_id, c.course_name
        FROM courses c
        JOIN teacher_courses tc ON c.course_id = tc.course_id
        WHERE tc.teacher_id=%s
    """, (session["teacher_id"],))
    courses = cursor.fetchall()

    analytics_data = []
    for course in courses:
        cursor.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status='Absent' THEN 1 ELSE 0 END) AS absent_count
            FROM attendance
            WHERE course_id=%s
        """, (course["course_id"],))
        result = cursor.fetchone()
        total = result["total"] or 0
        absent_count = result["absent_count"] or 0
        avg_absent = round((absent_count/total)*100,2) if total>0 else 0
        analytics_data.append({"course_name": course["course_name"], "avg_absent": avg_absent})

    cursor.close()
    db.close()
    return render_template("analytics.html", analytics_data=analytics_data)

# ---------- LOGOUT ----------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------- ADD COURSE ----------
@app.route("/add_course", methods=["GET", "POST"])
def add_course():
    if "teacher_id" not in session:
        return redirect(url_for("login"))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM classes ORDER BY class_name")
    classes = cursor.fetchall()

    if request.method == "POST":
        class_id = request.form["class_id"]
        course_name = request.form["course_name"]
        cursor.execute("INSERT INTO courses (course_name, class_id) VALUES (%s,%s)", (course_name, class_id))
        course_id = cursor.lastrowid
        cursor.execute("INSERT INTO teacher_courses (teacher_id, course_id) VALUES (%s,%s)", (session["teacher_id"], course_id))
        db.commit()
        cursor.close()
        db.close()
        flash(f"Course '{course_name}' added successfully!", "success")
        return redirect(url_for("classes"))

    cursor.close()
    db.close()
    return render_template("add_course.html", classes=classes)

# ---------- ADMIN ROUTES ----------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM admin WHERE email=%s", (email,))
        admin = cursor.fetchone()
        cursor.close()
        db.close()

        if admin and check_password_hash(admin["password_hash"], password):
            session["admin_id"] = admin["admin_id"]
            session["admin_name"] = admin["name"]
            return redirect(url_for("admin_dashboard"))
        else:
            error = "Invalid email or password"

    return render_template("admin/admin_login.html", error=error)

@app.route("/admin/dashboard")
def admin_dashboard():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM classes ORDER BY class_id")
    classes = cursor.fetchall()
    cursor.close()
    db.close()

    return render_template("admin/admin_dashboard.html", classes=classes)

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))

@app.route("/admin/students/<int:class_id>", methods=["GET", "POST"])
def admin_students(class_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM students WHERE class_id=%s ORDER BY student_id", (class_id,))
    students = cursor.fetchall()

    if request.method == "POST":
        new_name = request.form.get("student_name")
        if new_name:
            cursor.execute("INSERT INTO students (student_name, class_id) VALUES (%s, %s)", (new_name, class_id))
            db.commit()
            return redirect(url_for("admin_students", class_id=class_id))

    cursor.close()
    db.close()
    return render_template("admin/admin_students.html", students=students, class_id=class_id)

@app.route("/admin/delete_student/<int:student_id>", methods=["POST"])
def delete_student(student_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    db = get_db_connection()
    cursor = db.cursor()

    # Delete dependent attendance first
    cursor.execute("DELETE FROM attendance WHERE student_id=%s", (student_id,))
    # Then delete student
    cursor.execute("DELETE FROM students WHERE student_id=%s", (student_id,))
    
    db.commit()
    cursor.close()
    db.close()

    flash("Student deleted successfully!", "success")
    return redirect(request.referrer)

# ---------- ADMIN: COURSE STUDENTS ----------
@app.route("/admin/course/<int:course_id>/students")
def course_students(course_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Course info
    cursor.execute("SELECT course_name FROM courses WHERE course_id=%s", (course_id,))
    course = cursor.fetchone()
    course_name = course["course_name"] if course else "Course"

    # Students in this course
    cursor.execute("""
        SELECT s.student_id, s.student_name
        FROM course_students cs
        JOIN students s ON cs.student_id = s.student_id
        WHERE cs.course_id=%s
        ORDER BY s.student_id
    """, (course_id,))
    students = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "admin/course_students.html",
        students=students,
        course_name=course_name,
        course_id=course_id
    )

# ---------- RUN APP ----------
if __name__ == "__main__":
    app.run()