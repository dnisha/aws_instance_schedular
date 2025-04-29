import re
from datetime import datetime

def should_process_run(cron_expression: str, until_date: str) -> bool:
    """
    Check if the current time matches the cron expression and is on or before the until date.
    
    Args:
        cron_expression: A string in cron format (e.g., "5 1 * * *")
        until_date: A string in YYYY-MM-DD format (e.g., "2025-05-10")
        
    Returns:
        bool: True if the process should run now, False otherwise
    """
    try:
        # Parse cron expression
        cron_parts = re.split(r'\s+', cron_expression.strip())
        if len(cron_parts) != 5:
            raise ValueError("Invalid cron expression format")
            
        minute, hour, day_of_month, month, day_of_week = cron_parts
        
        # Parse until date (at midnight to include the entire day)
        until = datetime.strptime(until_date, "%Y-%m-%d").date()
        now = datetime.now()
        current_date = now.date()
        
        # Check if current date is after until date
        if current_date > until:
            return False
        
        # Check minute
        if minute != '*' and str(now.minute) != minute:
            return False
            
        # Check hour
        if hour != '*' and str(now.hour) != hour:
            return False
            
        # Check day of month
        if day_of_month != '*' and str(now.day) != day_of_month:
            return False
            
        # Check month
        if month != '*' and str(now.month) != month:
            return False
            
        # Check day of week (0-6 where 0 is Sunday)
        if day_of_week != '*':
            # Convert cron day of week to Python's weekday (0=Monday)
            cron_dow = int(day_of_week)
            # Sunday special case (cron: 0 or 7 = Sunday, Python: 6 = Sunday)
            if cron_dow == 0 or cron_dow == 7:
                if now.weekday() != 6:  # Python's Sunday
                    return False
            elif (cron_dow - 1) != now.weekday():
                return False
                
        # All checks passed - should run now
        return True
        
    except Exception as e:
        print(f"Error processing cron expression: {e}")
        return False


# Test cases
if __name__ == "__main__":
    # Test case 1: Should run at 01:05 on or before 2025-05-10
    cron_expr = "* * * * *"
    until_date = "2025-02-10"
    
    # Simulate current time being 2025-04-30 01:05:00
    test_time = datetime(2025, 4, 30, 1, 5)
    import mock
    with mock.patch('datetime.datetime') as mock_datetime:
        mock_datetime.now.return_value = test_time
        print(should_process_run(cron_expr, until_date))  # Should print True
    
    # Simulate current time being 2025-05-11 01:05:00 (after until_date)
    test_time = datetime(2025, 4, 29, 1, 5)
    with mock.patch('datetime.datetime') as mock_datetime:
        mock_datetime.now.return_value = test_time
        print(should_process_run(cron_expr, until_date))  # Should print False
    
    # Simulate current time being 2025-04-30 02:05:00 (wrong hour)
    test_time = datetime(2025, 4, 30, 2, 5)
    with mock.patch('datetime.datetime') as mock_datetime:
        mock_datetime.now.return_value = test_time
        print(should_process_run(cron_expr, until_date))  # Should print False