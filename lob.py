from dataclasses import dataclass
import statistics

@dataclass
class Order:
    id: int
    side: str
    price: int
    qty: int
    remaining: int
    cancelled: bool = False
    owner: object = None

    @property
    def status(self):
        if self.cancelled: return 'cancelled'
        return 'filled' if self.remaining == 0 else 'resting'

@dataclass
class Trade:
    price: int
    qty: int
    resting_id: int
    incoming_id: int

class Book:
    def __init__(self):
        self.orders = {}
        self.trades = []
        self.new_id = 0

    def rest(self, side, price, qty, append=True, owner=None):
        self.new_id += 1
        order = Order(self.new_id, side, price, qty, qty, owner=owner)
        if append: self.orders[self.new_id] = order
        return order

    def live_orders(self):
        live = []
        for o in self.orders.values():
            if o.remaining > 0: live.append(o)

        return live

    def best_bid(self):
        return max((o.price for o in self.live_orders() if o.side == "buy"), default=None)

    def best_ask(self):
        return min((o.price for o in self.live_orders() if o.side == "sell"), default=None)

    def show(self):
        bids, asks = self.l2(full=True)

        for price, qty in reversed(asks):
            print(f'    {price} | {qty}')

        print('-------------')

        for price, qty in bids:
            print(f'{qty} | {price}')

    def matchable(self, side, price):
        matchable_orders = []

        opposite = "buy" if side == "sell" else "sell"

        for o in self.live_orders():
            if o.side == opposite:
                if side == "buy" and o.price <= price:
                    matchable_orders.append(o)
                elif side == "sell" and o.price >= price:
                    matchable_orders.append(o)

        if side == "buy":
            matchable_orders.sort(key=lambda o: (o.price, o.id))  
        elif side == "sell":
            matchable_orders.sort(key=lambda o: (-o.price, o.id))

        return matchable_orders

    def submit(self, side, price, qty, owner=None, ioc=False):
        incoming = self.rest(side, price, qty, append=False, owner=owner)
        matchable_orders = self.matchable(side, price)

        trades = []

        for resting in matchable_orders:
            trade_qty = min(incoming.remaining, resting.remaining)
            trade = Trade(resting.price, trade_qty, resting.id, incoming.id)
            trades.append(trade)
            resting.remaining -= trade_qty
            incoming.remaining -= trade_qty
            if incoming.remaining == 0:
                break

        if incoming.remaining > 0 and ioc:
            incoming.remaining = 0
            incoming.cancelled = True

        self.orders[incoming.id] = incoming
        self.trades.extend(trades)

        return incoming.id, trades

    def check(self):
        bid, ask = self.best_bid(), self.best_ask()
        assert bid is None or ask is None or bid < ask, f"crossed: {bid} >= {ask}"

    def sanity(self, max_distance=50):
        if len(self.trades) > 0:
            prices = []
            for trade in self.trades[-100:]:
                prices.append(trade.price)

            for order in self.live_orders():
                assert abs((order.price - statistics.median(prices))) < max_distance, f"absurd resting order: {order.price}"

    def cancel(self, order_id):
        order = self.orders.get(order_id)
        if order is None or order.remaining == 0 or order.cancelled:
            return False
        order.cancelled = True
        order.remaining = 0
        return True

    def l2(self, depth=5, full=False):
        bids = []
        asks = []

        # ugly accumulation, fix later
        buy_price_levels = {}
        sell_price_levels = {}

        for o in self.live_orders():
                if o.side == "buy":
                    if o.price in buy_price_levels:
                        buy_price_levels[o.price] += o.remaining
                    else:
                        buy_price_levels[o.price] = o.remaining
                elif o.side == "sell":
                    if o.price in sell_price_levels:
                        sell_price_levels[o.price] += o.remaining
                    else:
                        sell_price_levels[o.price] = o.remaining

        for price, qty in sorted(buy_price_levels.items(), reverse=True):
            if len(bids) == depth and not full:
                break
            bids.append((price, qty))
        for price, qty in sorted(sell_price_levels.items()):
            if len(asks) == depth and not full:
                break
            asks.append((price, qty))

        return bids, asks
