# Notification bots

    ## Polymarket

    - [ ] Price notifications
        - [ ] Only send one notification per market in the 1m 5m 1h etc. buckets
        - [ ] Show the direction (up or down) of the price change
        - [ ] Only trigger a notif if the yes and no tokens have both changed in price
        - [ ] Once a notification has been sent, truncate the price to that time
        - [ ] Refactor the price time series to be a pandas series to make downstream logic easier

    - [ ] Price data
        - [ ] Parallelize getting the price data from markets
        - [X] Cache price data and only get it from the most recent time instead -- this ends up being slower, just pull 1d, thats probably cached on the server

    - [ ] Get volumne data from markets
    - [ ] More robust market closure conditions
    - [ ] Integrate the ability to trade from Telegram
    - [ ] Figure out how to scale this to multiple users

    - [X] Misc
        - [X] Get rid of the repeatedly running process on my machine


    ## Kalshi

    ## Pinnacle

# Ideas

- [ ] Use a Regex or OLlama to figure out which markets are the same -- "Potential Market Match" -- and let the user match them on Telegram if they are indeed the same

- [ ] Hit gamma endpoint to see if markets have resolved, and trade that resolution

