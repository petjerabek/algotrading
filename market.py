from lob import Book
import random

class FairValue:
    def __init__(self, start=10000, volatility=1, seed=None):
        self.value = start
        self.volatility = volatility
        self.rng = random.Random(seed)

    def step(self):
        self.value += round(self.rng.gauss(0, self.volatility))
        return self.value

class Trader:
    def __init__(self, name=None):
        self.name = name
        self.position = 0 # units held: positive long, negative short
        self.cash = 0 # signed cash flow

    def on_fill(self, side, price, qty):
         if side == 'buy':
            self.position += qty
            self.cash -= price * qty
         elif side == 'sell':
            self.position -= qty
            self.cash += price * qty

    def pnl(self, mark_price):
         return self.cash + self.position * mark_price

class Maker(Trader):
    def __init__(self, noise=2, offset=3, size=10, activity=0.5, seed=None, name=None):
        super().__init__(name)
        self.noise = noise
        self.offset = offset
        self.size = size
        self.activity = activity
        self.rng = random.Random(seed)
        self.last_id = None

    def act(self, book, fair_value):
        if self.rng.random() < self.activity:
            if self.last_id is not None:
                        book.cancel(self.last_id)
                        self.last_id = None

            estimate = fair_value + round(self.rng.gauss(0, self.noise))
            sides = ['buy', 'sell']
            side = self.rng.choice(sides)
            price = estimate - self.offset if side == 'buy' else estimate + self.offset

            self.last_id, trades = book.submit(side, price, self.size, owner=self)
            dispatch(book, trades)

class Taker(Trader):
     def __init__(self, size=10, rate=0.1, seed=None, name=None):
          super().__init__(name)
          self.size = size
          self.rate = rate
          self.rng = random.Random(seed)

     def act(self, book):
        if self.rng.random() < self.rate:
            price = None
            sides = ['buy', 'sell']
            side = self.rng.choice(sides)

            if side == 'buy':
                best_ask = book.best_ask()
                if best_ask is not None:
                    price = best_ask + 10000

            elif side == 'sell':
                best_bid = book.best_bid()
                if best_bid is not None:
                    price = best_bid - 10000

            if price is not None:
                id, trades = book.submit(side, price, self.size, ioc=True, owner=self)
                dispatch(book, trades)

def dispatch(book, trades):
     for trade in trades:
          resting_order = book.orders[trade.resting_id]
          incoming_order = book.orders[trade.incoming_id]

          if resting_order.owner is not None:
               resting_order.owner.on_fill(resting_order.side, trade.price, trade.qty)

          if incoming_order.owner is not None:
               incoming_order.owner.on_fill(incoming_order.side, trade.price, trade.qty)


from lob import Book

fv = FairValue(start=10000, volatility=1, seed=7)
b = Book()
makers = [Maker(noise=2, offset=3, seed=100 + i) for i in range(20)]
takers = [Taker(seed=100 + i) for i in range(20)]
all_traders = makers + takers

for _ in range(200):
    v = fv.step()
    for m in makers:
         m.act(b, v)
    for t in takers:
         t.act(b)
    b.check()

    best_ask = b.best_ask()
    best_bid = b.best_bid()
    
    if best_ask is not None and best_bid is not None:
        mid = (best_ask + best_bid) / 2
        total = sum(t.pnl(mid) for t in all_traders)
        assert abs(total) < 1e-9, f"PnL doesn't sum to zero: {total}"

        makers_pnl = sum(m.pnl(mid) for m in makers)
        takers_pnl = sum(t.pnl(mid) for t in takers)
        print(f'Makers pnl: {makers_pnl} | Takers pnl: {takers_pnl}')

b.show()