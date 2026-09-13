#!/usr/bin/env python3
import time
import schedule
import datetime
from auto_pipeline import run_pipeline

def job():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n⏰ [{now}] Starting Scheduled Autonomous Video Generation & Post...")
    try:
        run_pipeline(generate_only=False)
    except Exception as e:
        print(f"❌ Scheduled run failed: {e}")

# Schedule 3 daily auto-posts matching peak Instagram/Shorts engagement in India
schedule.every().day.at("10:00").do(job)  # Morning Slot
schedule.every().day.at("15:00").do(job)  # Afternoon Slot
schedule.every().day.at("19:30").do(job)  # Evening Peak Slot

if __name__ == "__main__":
    print("==========================================================")
    print("🤖 DAILYGEOMAP BACKGROUND SCHEDULER ACTIVE")
    print("  Scheduled Slots (IST): 10:00 AM | 3:00 PM | 7:30 PM")
    print("  Press Ctrl+C to stop.")
    print("==========================================================")
    
    # Run once immediately on start
    job()

    while True:
        schedule.run_pending()
        time.sleep(30)
