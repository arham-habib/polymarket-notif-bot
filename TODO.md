# Notification bots

    ## Polymarket

    - [x] Deal with the issue log below -> safe send message

    - [ ] Why appending all the extra cursors here?

    - [ ] Telegram bot add multiple tags at once

    - [ ] Add a fourth layer of abstraction: tracked_market.json such that if the user changes the config, there will be an update.

    - [ ] Bot commands
      - [ ] help
      - [ ] Market (get it to send the condition_id)
      - [ ] List_attributes
      - [ ] add more than one attribute at once to the tags
  
      - [ ] Volume
      - [ ] Spread
      - [ ] Update config
      - [ ] Catch all handler for bad commands

    - [ ] Have not implemented price or volume thresholds
    - [ ] Add max_expiry dates for polymarket
       - [X] Add to code
       - [ ] Add to config file

    - [ ] Connection reset from peer (?)



    ## Kalshi

    ## Pinnacle

# Ideas

- Use a regex or OLlama to figure out which markets are the same -- "Potential Market Match" -- and let the user match them on telegram if they are

- Large price diff tracker
    - .1 bid ask spread
    - $30k volume
    - econ subject
    - 100 dollars on the book for .1 spread
    - .1 


2025-01-15 22:22:45,012 - root - INFO - Found 22699 known markets and 51 cursors. Skipping initial pass.
2025-01-15 22:24:45,466 - root - INFO - Starting new check from cursor: MjQ1MDA=
Traceback (most recent call last):
  File "/Users/arhamhabib/GitHub/polymarket-notif-bot/polymarket_notification_bot.py", line 267, in <module>
    main()
  File "/Users/arhamhabib/GitHub/polymarket-notif-bot/polymarket_notification_bot.py", line 262, in main
    schedule.run_pending()
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/schedule/__init__.py", line 854, in run_pending
    default_scheduler.run_pending()
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/schedule/__init__.py", line 101, in run_pending
    self._run_job(job)
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/schedule/__init__.py", line 173, in _run_job
    ret = job.run()
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/schedule/__init__.py", line 691, in run
    ret = self.job_func()
  File "/Users/arhamhabib/GitHub/polymarket-notif-bot/polymarket_notification_bot.py", line 226, in check_new_markets
    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/telegram/bot.py", line 134, in decorator
    result = func(*args, **kwargs)
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/telegram/bot.py", line 534, in send_message
    return self._message(  # type: ignore[return-value]
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/telegram/bot.py", line 344, in _message
    result = self._post(endpoint, data, timeout=timeout, api_kwargs=api_kwargs)
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/telegram/bot.py", line 299, in _post
    return self.request.post(
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/telegram/utils/request.py", line 361, in post
    result = self._request_wrapper(
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/telegram/utils/request.py", line 272, in _request_wrapper
    message = str(self._parse(resp.data))
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/telegram/utils/request.py", line 230, in _parse
    raise RetryAfter(retry_after)



    Traceback (most recent call last):
  File "/Users/arhamhabib/GitHub/polymarket-notif-bot/src/main.py", line 224, in <module>
    bot.start()
  File "/Users/arhamhabib/GitHub/polymarket-notif-bot/src/main.py", line 201, in start
    schedule.run_pending()
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/schedule/__init__.py", line 854, in run_pending
    default_scheduler.run_pending()
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/schedule/__init__.py", line 101, in run_pending
    self._run_job(job)
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/schedule/__init__.py", line 173, in _run_job
    ret = job.run()
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/schedule/__init__.py", line 691, in run
    ret = self.job_func()
  File "/Users/arhamhabib/GitHub/polymarket-notif-bot/src/main.py", line 124, in check_markets
    all_active_markets, new_markets, new_cursors, closed_market_condition_ids, closed_markets_schema = polymarket_get_new_markets(
  File "/Users/arhamhabib/GitHub/polymarket-notif-bot/src/utils/find_new_markets.py", line 81, in polymarket_get_new_markets
    Params: 
  File "/Users/arhamhabib/GitHub/polymarket-notif-bot/src/utils/find_new_markets.py", line 145, in _polymarket_crawl_markets
    response = client.get_markets(next_cursor=cursor)
  File "/Users/arhamhabib/GitHub/polymarket-notif-bot/src/utils/find_new_markets.py", line 128, in _polymarket_get_markets_page
    updated_active_markets[condition_id] = market
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/py_clob_client/client.py", line 711, in get_markets
    return get("{}{}?next_cursor={}".format(self.host, GET_MARKETS, next_cursor))
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/py_clob_client/http_helpers/helpers.py", line 58, in get
    return request(endpoint, GET, headers, data)
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/site-packages/py_clob_client/http_helpers/helpers.py", line 50, in request
    raise PolyApiException(error_msg="Request exception!")
py_clob_client.exceptions.PolyApiException: PolyApiException[status_code=None, error_message=Request exception!]
^CException ignored in: <module 'threading' from '/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/threading.py'>
Traceback (most recent call last):
  File "/Users/arhamhabib/miniforge3/envs/np_veclib/lib/python3.9/threading.py", line 1477, in _shutdown