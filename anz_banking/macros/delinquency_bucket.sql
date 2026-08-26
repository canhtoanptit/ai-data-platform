{#
    Reusable macro: turn a "days past due" integer into a standard
    collections delinquency bucket. Used across intermediate/mart models so
    the bucket logic lives in exactly one place.

    Usage:  {{ delinquency_bucket('days_past_due') }} as delinquency_bucket
#}
{% macro delinquency_bucket(days_past_due_column) %}
    case
        when {{ days_past_due_column }} <= 0  then 'current'
        when {{ days_past_due_column }} <= 30 then '1-30 dpd'
        when {{ days_past_due_column }} <= 60 then '31-60 dpd'
        when {{ days_past_due_column }} <= 90 then '61-90 dpd'
        else '90+ dpd'
    end
{% endmacro %}
