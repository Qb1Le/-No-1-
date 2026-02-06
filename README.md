## Установка

### 1. Клонирование репозитория
```bash
git clone https://github.com/your-username/ExamArena.git
cd ExamArena
```

### 2. Активируйте venv
**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**Windows (CMD):**
```cmd
python -m venv venv
venv\Scripts\activate
```

**Linux/Mac:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Установите зависимости
```bash
pip install -r requirements.txt
```

## Запуск

```bash
python app.py
```

Сайт будет доступен по адресу: `http://localhost:5000`