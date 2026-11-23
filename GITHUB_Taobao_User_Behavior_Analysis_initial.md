# Taobao User Behavior Funnel and Segmentation Analysis Initial

## Project overview

This project analyzes anonymized Taobao user behavior logs to explore the path from product view to favorite, cart, and purchase. It also applies a simplified user segmentation approach based on purchase recency and purchase behavior frequency.

## Initial findings

The initial user-level time-ordered analysis found:

- 984,105 users with a view event;
- 763,076 users with a later favorite or cart event;
- 368,431 users with a later purchase event;
- user-level view to purchase rate of 37.44% under the initial window-based definition.

A first item-level analysis produced a much lower view-to-purchase rate of about 0.13%.

## Data

Source: [Alibaba Tianchi Taobao User Behavior Dataset](https://tianchi.aliyun.com/dataset/dataDetail?dataId=649)

Fields:

```text
user_id, item_id, category_id, behavior_type, timestamp
```

Behavior types:

```text
pv, fav, cart, buy
```

## Initial methods

- Timestamp conversion to China Standard Time;
- Filtering to the documented observation period;
- Chunked Pandas and DuckDB processing;
- User-level behavior funnel;
- Initial RFM-style user grouping;
- Basic date, hour, and category breakdowns.

## Initial limitations

The initial version did not fully separate behavior coverage from conversion rate. It did not provide a complete metric dictionary, fixed follow-up windows, session paths, out-of-sample segment validation, or amount/order metrics. The initial numbers should therefore be treated as exploratory results rather than final business KPIs.

## Repository structure

```text
data/raw/           original data, not committed
data/processed/     cleaned data and result tables
src/                analysis scripts
figures/            charts
README.md           project entry point
```
