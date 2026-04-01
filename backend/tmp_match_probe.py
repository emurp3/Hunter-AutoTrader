from datetime import date, timedelta
from app.models.budget import WeeklyBudget
from app.services.budget import _compute_month_end_review

cases = [
    ('bankroll_150_before_end', WeeklyBudget(starting_bankroll=100, current_bankroll=150, evaluation_start_date=date.today()-timedelta(days=2), evaluation_end_date=date.today()+timedelta(days=28))),
    ('bankroll_200_before_end', WeeklyBudget(starting_bankroll=100, current_bankroll=200, evaluation_start_date=date.today()-timedelta(days=2), evaluation_end_date=date.today()+timedelta(days=28))),
    ('bankroll_250_before_end', WeeklyBudget(starting_bankroll=100, current_bankroll=250, evaluation_start_date=date.today()-timedelta(days=2), evaluation_end_date=date.today()+timedelta(days=28))),
    ('bankroll_250_after_end', WeeklyBudget(starting_bankroll=100, current_bankroll=250, evaluation_start_date=date.today()-timedelta(days=35), evaluation_end_date=date.today()-timedelta(days=1))),
    ('bankroll_90_after_end', WeeklyBudget(starting_bankroll=100, current_bankroll=90, evaluation_start_date=date.today()-timedelta(days=35), evaluation_end_date=date.today()-timedelta(days=1))),
]

for name, budget in cases:
    review = _compute_month_end_review(budget)
    print(name)
    print({
        'capital_match_eligible': review['capital_match_eligible'],
        'capital_match_amount': review['recommended_match_amount'],
        'progress_to_doubling_threshold': review['progress_to_doubling_threshold'],
        'evaluation_window_closed': review['evaluation_window_closed'],
    })
