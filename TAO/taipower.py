from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
import csv
import time

def query_taipower(park, dist, cpsno):
    print(f"查詢 {park} ({dist}, {cpsno}) ...")
    options = Options()
    # 取消 headless 方便觀察
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.7204.185 Safari/537.36"
    )

    driver = webdriver.Chrome(options=options)
    driver.get("https://service.taipower.com.tw/wapp/newnas/nawp090.aspx")

    try:
        # 等待表單出現
        WebDriverWait(driver, 20).until(
            EC.visibility_of_element_located((By.ID, "rb_cps"))
        )

        # 填入資料
        driver.find_element(By.ID, "custname").send_keys("台達電")
        driver.find_element(By.ID, "rb_cps").click()
        select_dist = Select(driver.find_element(By.ID, "dist"))
        select_dist.select_by_value(dist)
        driver.find_element(By.ID, "cpsno").send_keys(cpsno)
        driver.find_element(By.ID, "Button2").click()

        # 等待結果
        WebDriverWait(driver, 20).until(
            EC.visibility_of_element_located((By.ID, "LL_item"))
        )

        item = driver.find_element(By.ID, "LL_item").text
        status = driver.find_element(By.ID, "LL_status").text

        print(f"{park} 查詢完成！")
        return item, status

    except Exception as e:
        print(f"{park} 查詢失敗:", e)
        # 印出 page source 方便 debug
        with open("debug_page.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        return "查無資料", "查無資料"

    finally:
        driver.quit()


if __name__ == "__main__":
    with open("case.csv", newline="", encoding="utf-8-sig") as f:  # 去除 BOM
        reader = csv.DictReader(f)
        reader.fieldnames = [name.strip() for name in reader.fieldnames]  # 去掉欄位空白
        results = []

        for row in reader:
            park = row["park"].strip()
            dist = row["dist"].strip()
            cpsno = row["cpsno"].strip()
            item, status = query_taipower(park, dist, cpsno)
            results.append([park, item, status])

    # 寫入結果到 result.txt
    with open("result.txt", "w", encoding="utf-8") as f:
        f.write("park,item,status\n")
        for park, item, status in results:
            f.write(f"{park},{item},{status}\n")

    print("✅ 所有查詢完成，結果已寫入 result.txt")
