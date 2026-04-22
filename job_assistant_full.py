#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Job Assistant — локальный помощник для поиска IT-вакансий
Подробнее: https://github.com/findjob
"""

import os
import json
import sqlite3
import time
import requests
import threading
import schedule
from datetime import datetime, date, timedelta
from flask import Flask, render_template_string, request, jsonify
from openai import OpenAI
import argparse

# ==================================================
# НАСТРОЙКИ (ЗАМЕНИ НА СВОИ!)
# ==================================================

# 1. API-ключ DeepSeek (обязательно)
# Получить здесь: https://platform.deepseek.com/
DEEPSEEK_API_KEY = ""  # Вставь сюда свой ключ, например: "sk-xxxxxxxxxxxx"

# 2. Настройки почты для ежедневной рассылки (опционально)
# Если не нужна рассылка, оставь поля пустыми
EMAIL_SENDER = ""       # Твой ящик, например: "your_email@yandex.ru"
EMAIL_PASSWORD = ""     # Пароль приложения (не обычный пароль!)
EMAIL_RECEIVER = ""     # Куда отправлять отчёт, например: "your_email@gmail.com"

# 3. Параметры поиска для ежедневной рассылки и рекомендаций
# Измени под свои предпочтения
DEFAULT_KEYWORDS = ["Python", "Java", "Backend"]   # Ключевые слова для поиска, вставь свои, тут я указала пример
DEFAULT_EXPERIENCE = "between3And6"                # Опыт: noExperience, between1And3, between3And6, moreThan6, здесь пример опыта, который я для себя настроила

# ==================================================
# ОСТАЛЬНОЙ КОД НЕ ТРЕБУЕТ ИЗМЕНЕНИЙ
# ==================================================

DB_PATH = "jobs_database.db"
app = Flask(__name__)

# Подключение к DeepSeek
def get_ai_client():
    if not DEEPSEEK_API_KEY:
        return None
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

# Инициализация базы данных
def init_database():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS jobs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT, company TEXT, url TEXT, description TEXT,
                  score INTEGER, analysis TEXT, status TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS resumes
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  job_id INTEGER, adapted_resume TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS applications
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  job_title TEXT, company TEXT, date_applied DATE,
                  status TEXT, next_followup DATE, notes TEXT,
                  contact_person TEXT, salary_offered INTEGER,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

# Поиск вакансий на hh.ru
def search_hh_vacancies(keyword, experience='between3And6', per_page=15):
    url = "https://api.hh.ru/vacancies"
    params = {
        'text': keyword,
        'area': 113,  # Россия
        'experience': experience,
        'per_page': per_page,
        'order_by': 'publication_time'
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        vacancies = []
        for item in data.get('items', []):
            desc_url = f"https://api.hh.ru/vacancies/{item['id']}"
            desc_resp = requests.get(desc_url, timeout=10)
            desc = ""
            if desc_resp.status_code == 200:
                desc_data = desc_resp.json()
                desc = desc_data.get('description', '')
                import re
                desc = re.sub(r'<[^>]+>', '', desc)
            salary_text = ""
            s = item.get('salary')
            if s:
                if s.get('from') and s.get('to'):
                    salary_text = f"{s['from']} - {s['to']} {s.get('currency', '')}"
                elif s.get('from'):
                    salary_text = f"от {s['from']} {s.get('currency', '')}"
                elif s.get('to'):
                    salary_text = f"до {s['to']} {s.get('currency', '')}"
            vacancies.append({
                'id': item['id'],
                'title': item['name'],
                'company': item['employer']['name'],
                'url': item['alternate_url'],
                'salary': salary_text,
                'description': desc
            })
            time.sleep(0.3)
        return vacancies
    except Exception as e:
        print(f"Ошибка поиска: {e}")
        return []

# Поиск вакансий по конкретной компании (по ID на hh.ru)
def search_company_vacancies(company_id, per_page=15):
    url = "https://api.hh.ru/vacancies"
    params = {'employer_id': company_id, 'area': 113, 'per_page': per_page, 'order_by': 'publication_time'}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        vacancies = []
        for item in data.get('items', []):
            salary_text = ""
            s = item.get('salary')
            if s:
                if s.get('from') and s.get('to'):
                    salary_text = f"{s['from']} - {s['to']} {s.get('currency', '')}"
                elif s.get('from'):
                    salary_text = f"от {s['from']} {s.get('currency', '')}"
                elif s.get('to'):
                    salary_text = f"до {s['to']} {s.get('currency', '')}"
            vacancies.append({
                'title': item['name'],
                'company': item['employer']['name'],
                'url': item['alternate_url'],
                'salary': salary_text,
                'description': ""
            })
            time.sleep(0.3)
        return vacancies
    except Exception as e:
        print(f"Ошибка: {e}")
        return []

# ==================================================
# СПИСОК КОМПАНИЙ ДЛЯ ОТДЕЛЬНОЙ ВКЛАДКИ
# ЗАМЕНИ ID КОМПАНИЙ НА СВОИ! Я ИСКАЛА И ПИСАЛА ДЛЯ СЕБЯ!
# ID можно найти в URL вакансии на hh.ru: ?employer_id=XXXX
# ==================================================
def get_favorite_companies():
    return [
        {'name': 'Пример компании 1', 'hh_id': '0000', 'url': 'https://example.com/career'},
        {'name': 'Пример компании 2', 'hh_id': '0000', 'url': 'https://example.com/career'},
        # Добавь свои компании по образцу
    ]

# ==================================================
# ТВОЁ РЕЗЮМЕ ДЛЯ АВТОМАТИЧЕСКОЙ ОЦЕНКИ
# ЗАМЕНИ НА ТЕКСТ СВОЕГО РЕЗЮМЕ (в двух местах ниже)
# ==================================================
MY_RESUME_TEMPLATE = """
Здесь напиши текст своего резюме.
Например:
- Опыт работы: Python разработчик 5 лет
- Ключевые навыки: Django, FastAPI, PostgreSQL, Docker
- Образование: ...
"""

# Оценка вакансии AI
def score_vacancy(vacancy_title, vacancy_desc, resume_text):
    client = get_ai_client()
    if not client:
        return 5, "API не настроен"
    prompt = f"""Оцени соответствие вакансии и резюме от 0 до 10. Ответь ТОЛЬКО JSON: {{"score": число, "comment": "короткая фраза"}}

Вакансия: {vacancy_title}
Описание: {vacancy_desc[:3000]}

Резюме: {resume_text[:3000]}"""
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"},
            max_tokens=500
        )
        result = json.loads(resp.choices[0].message.content)
        score = min(10, max(0, int(result.get('score', 5))))
        return score, result.get('comment', '')
    except Exception as e:
        return 5, f"ошибка: {str(e)[:50]}"

# Полный анализ вакансии
def analyze_job_fit(job_description, resume_text):
    client = get_ai_client()
    if not client:
        return {'score': 5, 'strengths': ['API не настроен'], 'weaknesses': ['Проверь ключ'], 'recommendation': 'Настрой API'}
    prompt = f"""Ты эксперт по найму. Оцени кандидата. Ответь ТОЛЬКО JSON:
{{"score": число от 1 до 10, "strengths": ["плюс1", "плюс2"], "weaknesses": ["минус1"], "recommendation": "стоит ли откликаться?"}}

Вакансия: {job_description}

Резюме: {resume_text}"""
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"},
            max_tokens=2000
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        return {'score': 5, 'strengths': [f'Ошибка: {str(e)[:100]}'], 'weaknesses': ['Повтори попытку'], 'recommendation': 'Попробуй ещё раз'}

# Адаптация резюме под вакансию
def adapt_resume(job_description, resume_text):
    client = get_ai_client()
    if not client:
        return "Ошибка: API не настроен"
    prompt = f"""Адаптируй резюме под вакансию. Не выдумывай навыки, которых нет в исходном резюме.
Сохрани все даты, названия компаний и ключевые достижения.

Вакансия:
{job_description}

Исходное резюме:
{resume_text}

Выдай ТОЛЬКО адаптированное резюме."""
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=8000
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"Ошибка: {e}"

# Генерация вопросов для собеседования
def generate_interview_questions(job_description, company, resume_text):
    client = get_ai_client()
    if not client:
        return "Ошибка API"
    prompt = f"""Компания: {company}
Вакансия: {job_description}

Резюме кандидата: {resume_text}

Сгенерируй 5 технических и 5 поведенческих вопросов с краткими советами по ответам."""
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=3000
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"Ошибка: {e}"

# Анализ зарплаты
def analyze_salary(job_title, city, experience_years):
    client = get_ai_client()
    if not client:
        return {"error": "API не настроен"}
    prompt = f"""Ты HR-аналитик. Определи рыночную зарплату для позиции {job_title} в городе {city} с опытом {experience_years} лет.
Ответь ТОЛЬКО JSON:
{{
    "min_salary": число,
    "average_salary": число,
    "max_salary": число,
    "currency": "RUB",
    "factors": ["фактор1", "фактор2"],
    "negotiation_tips": ["совет1", "совет2"],
    "counter_offer_script": "текст для контрпредложения"
}}"""
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"},
            max_tokens=800
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        return {"error": str(e)}

# Генерация сопроводительного письма
def generate_cover_letter(job_title, company, experience, achievements):
    client = get_ai_client()
    if not client:
        return "Ошибка API"
    prompt = f"""Напиши сопроводительное письмо для позиции {job_title} в компанию {company}.
Опыт кандидата: {experience}
Ключевые достижения: {achievements}
Длина 150-200 слов, профессиональный стиль, на русском."""
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"Ошибка: {e}"

# Анализ трендов рынка
def analyze_trends(technology):
    client = get_ai_client()
    if not client:
        return {"error": "API не настроен"}
    prompt = f"""Проанализируй рынок для технологии/роли: {technology}.
Ответь JSON:
{{
    "demand": число от 1 до 10,
    "trend": "растет/падает/стабильно",
    "active_companies": ["компания1", "компания2"],
    "additional_skills": ["навык1", "навык2"],
    "salary_junior": "число",
    "salary_middle": "число",
    "salary_senior": "число",
    "forecast": "прогноз на год"
}}"""
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"},
            max_tokens=600
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        return {"error": str(e)}

# Трекер откликов
def add_application(job_title, company, contact=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = date.today()
    followup = today + timedelta(days=7)
    c.execute('''INSERT INTO applications (job_title, company, date_applied, status, next_followup, contact_person)
                 VALUES (?, ?, ?, ?, ?, ?)''', (job_title, company, today, 'pending', followup, contact))
    conn.commit()
    app_id = c.lastrowid
    conn.close()
    return app_id

def get_applications():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, job_title, company, date_applied, status, next_followup FROM applications ORDER BY date_applied DESC')
    rows = c.fetchall()
    conn.close()
    apps = []
    for row in rows:
        apps.append({
            'id': row[0],
            'job_title': row[1],
            'company': row[2],
            'date_applied': row[3],
            'status': row[4],
            'next_followup': row[5]
        })
    return apps

def update_application_status(app_id, status):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE applications SET status = ? WHERE id = ?', (status, app_id))
    conn.commit()
    conn.close()

def generate_report():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT status, COUNT(*) FROM applications GROUP BY status')
    stats = dict(c.fetchall())
    total = sum(stats.values())
    report = f"📊 Статистика откликов\nВсего: {total}\nВ обработке: {stats.get('pending',0)}\nПриглашения: {stats.get('interview',0)}\nОтказы: {stats.get('rejected',0)}\nОфферы: {stats.get('offer',0)}\nПринято: {stats.get('accepted',0)}"
    conn.close()
    return report

# Ежедневная рассылка
def send_daily_report():
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("Почта не настроена, рассылка отключена")
        return
    try:
        import yagmail
    except ImportError:
        print("Установи yagmail: pip install yagmail")
        return

    my_resume = MY_RESUME_TEMPLATE  # Используем шаблон резюме

    print("📧 Формирую ежедневный отчёт...")
    all_good_jobs = []

    for keyword in DEFAULT_KEYWORDS:
        vacancies = search_hh_vacancies(keyword, experience=DEFAULT_EXPERIENCE, per_page=10)
        print(f"  По ключу '{keyword}' найдено {len(vacancies)} вакансий")
        for vac in vacancies:
            score, comment = score_vacancy(vac['title'], vac['description'], my_resume)
            if score >= 7:
                all_good_jobs.append({
                    'title': vac['title'],
                    'company': vac['company'],
                    'url': vac['url'],
                    'salary': vac['salary'],
                    'score': score,
                    'comment': comment
                })
            time.sleep(0.5)

    if not all_good_jobs:
        body = "🔔 За сегодня новых подходящих вакансий не найдено."
    else:
        all_good_jobs.sort(key=lambda x: x['score'], reverse=True)
        body = f"🔔 Ежедневная подборка релевантных вакансий (опыт: {DEFAULT_EXPERIENCE})\n\n"
        for job in all_good_jobs[:20]:
            body += f"🏢 {job['company']}\n📌 {job['title']}\n💰 {job['salary']}\n⭐ Оценка: {job['score']}/10 — {job['comment']}\n🔗 {job['url']}\n\n"

    try:
        yag = yagmail.SMTP(EMAIL_SENDER, EMAIL_PASSWORD)
        yag.send(to=EMAIL_RECEIVER, subject="Ежедневный отчёт о вакансиях", contents=body)
        print(f"✅ Отчёт отправлен на {EMAIL_RECEIVER}")
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")

def start_scheduler():
    schedule.every().day.at("09:00").do(send_daily_report)
    while True:
        schedule.run_pending()
        time.sleep(60)

# ==================================================
# ВЕБ-ИНТЕРФЕЙС (HTML)
# Он не очень красивый, но функциональный
# ==================================================
# тут я передумала писать
# ==================================================

# API маршруты
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)  # HTML_TEMPLATE 

@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    data = request.json
    return jsonify(analyze_job_fit(data['job_desc'], data['resume']))

@app.route('/api/adapt', methods=['POST'])
def api_adapt():
    data = request.json
    adapted = adapt_resume(data['job_desc'], data['resume'])
    return jsonify({'adapted': adapted})

@app.route('/api/interview', methods=['POST'])
def api_interview():
    data = request.json
    res = generate_interview_questions(data['job_desc'], data['company'], data['resume'])
    return jsonify({'questions': res})

@app.route('/api/recommend', methods=['POST'])
def api_recommend():
    data = request.json
    keyword = data['keyword']
    experience = data.get('experience', 'between3And6')
    vacancies = search_hh_vacancies(keyword, experience=experience, per_page=20)
    my_resume = MY_RESUME_TEMPLATE
    good = []
    for v in vacancies:
        score, comment = score_vacancy(v['title'], v['description'], my_resume)
        if score >= 6:
            good.append({
                'title': v['title'],
                'company': v['company'],
                'url': v['url'],
                'salary': v['salary'],
                'score': score,
                'comment': comment
            })
        time.sleep(0.3)
    good.sort(key=lambda x: x['score'], reverse=True)
    return jsonify(good[:20])

@app.route('/api/auto_companies', methods=['GET'])
def api_auto_companies():
    companies = get_favorite_companies()
    my_resume = MY_RESUME_TEMPLATE
    result = []
    for comp in companies:
        vacs = search_company_vacancies(comp['hh_id'], per_page=10)
        scored = []
        for v in vacs:
            score, comment = score_vacancy(v['title'], v['title'], my_resume)
            if score >= 6:
                v['score'] = score
                v['comment'] = comment
                scored.append(v)
        scored.sort(key=lambda x: x['score'], reverse=True)
        result.append({'name': comp['name'], 'vacancies': scored[:8]})
    return jsonify({'companies': result})

@app.route('/api/salary_analysis', methods=['POST'])
def api_salary():
    data = request.json
    res = analyze_salary(data['job_title'], data['city'], data['experience'])
    return jsonify(res)

@app.route('/api/cover_letter', methods=['POST'])
def api_cover():
    data = request.json
    letter = generate_cover_letter(data['job_title'], data['company'], data.get('experience',''), data.get('achievements',''))
    return jsonify({'letter': letter})

@app.route('/api/tracker_add', methods=['POST'])
def api_tracker_add():
    data = request.json
    app_id = add_application(data['job_title'], data['company'], data.get('contact',''))
    return jsonify({'id': app_id})

@app.route('/api/tracker_get', methods=['GET'])
def api_tracker_get():
    apps = get_applications()
    return jsonify(apps)

@app.route('/api/tracker_update', methods=['POST'])
def api_tracker_update():
    data = request.json
    update_application_status(data['id'], data['status'])
    return jsonify({'ok': True})

@app.route('/api/tracker_report', methods=['GET'])
def api_tracker_report():
    report = generate_report()
    return jsonify({'report': report})

@app.route('/api/trends', methods=['POST'])
def api_trends():
    data = request.json
    res = analyze_trends(data['technology'])
    return jsonify(res)

# Запуск
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5000)
    args = parser.parse_args()

    if not DEEPSEEK_API_KEY:
        print("=" * 50)
        print("⚠️ ВНИМАНИЕ: Не указан API-ключ DeepSeek!")
        print("Получить ключ: https://platform.deepseek.com/")
        print("=" * 50)
        return

    init_database()

    if EMAIL_SENDER and EMAIL_PASSWORD and EMAIL_RECEIVER:
        th = threading.Thread(target=start_scheduler, daemon=True)
        th.start()
        print(f"📅 Ежедневная рассылка включена (в 9:00) на адрес {EMAIL_RECEIVER}")
    else:
        print("📧 Рассылка отключена: не указаны почтовые настройки")

    print(f"🚀 Сервер запущен на http://localhost:{args.port}")
    app.run(host='0.0.0.0', port=args.port, debug=False)

if __name__ == '__main__':
    main()
