package pe.axiz.payment.adapters.out.postgresql;

import java.sql.*;
import java.time.*;
import java.util.*;
import org.springframework.jdbc.core.*;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.support.TransactionTemplate;
import pe.axiz.payment.application.ports.out.PaymentStore;
import pe.axiz.payment.domain.*;
import tools.jackson.databind.ObjectMapper;

@Repository
public class JdbcPaymentStore implements PaymentStore {
  private final JdbcTemplate jdbc;
  private final TransactionTemplate tx;
  private final ObjectMapper json;

  public JdbcPaymentStore(JdbcTemplate jdbc, TransactionTemplate tx, ObjectMapper json) {
    this.jdbc = jdbc;
    this.tx = tx;
    this.json = json;
  }

  private final RowMapper<Payment> mapper =
      (r, i) ->
          new Payment(
              r.getObject("id", UUID.class),
              r.getString("merchant_id"),
              new Money(r.getBigDecimal("amount"), r.getString("currency")),
              r.getString("token"),
              r.getString("idempotency_key"),
              r.getString("fingerprint"),
              Payment.Status.valueOf(r.getString("status")),
              r.getString("authorization_code"),
              r.getTimestamp("created_at").toInstant());

  @Override
  public Reservation reserve(Payment p) {
    return tx.execute(
        s -> {
          int n =
              jdbc.update(
                  "INSERT INTO payments(id,merchant_id,amount,currency,token,idempotency_key,fingerprint,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(merchant_id,idempotency_key) DO NOTHING",
                  p.id(),
                  p.merchantId(),
                  p.money().amount(),
                  p.money().currency(),
                  p.token(),
                  p.idempotencyKey(),
                  p.fingerprint(),
                  p.status().name(),
                  Timestamp.from(p.createdAt()),
                  Timestamp.from(p.createdAt()));
          var result =
              jdbc.queryForObject(
                  "SELECT * FROM payments WHERE merchant_id=? AND idempotency_key=?",
                  mapper,
                  p.merchantId(),
                  p.idempotencyKey());
          return new Reservation(result, n == 1);
        });
  }

  @Override
  public Payment complete(Payment p) {
    return tx.execute(
        s -> {
          int n =
              jdbc.update(
                  "UPDATE payments SET status=?,authorization_code=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('PENDING','UNKNOWN')",
                  p.status().name(),
                  p.authorizationCode(),
                  p.id());
          if (n == 1 && p.terminal()) {
            var event = new LinkedHashMap<String, Object>();
            event.put("eventId", p.id() + "-final");
            event.put("paymentId", p.id());
            event.put("merchantId", p.merchantId());
            event.put("amount", p.money().amount());
            event.put("currency", p.money().currency());
            event.put("status", p.status());
            event.put("authorizationCode", p.authorizationCode());
            event.put("schemaVersion", 1);
            String body = json.writeValueAsString(event);
            for (String sink : List.of("SNS", "KAFKA", "EVENTBRIDGE"))
              jdbc.update(
                  "INSERT INTO outbox(id,payment_id,destination,payload) VALUES(?,?,?,?)",
                  UUID.randomUUID(),
                  p.id(),
                  sink,
                  body);
          }
          return find(p.id()).orElseThrow();
        });
  }

  @Override
  public Optional<Payment> find(UUID id) {
    return jdbc.query("SELECT * FROM payments WHERE id=?", mapper, id).stream().findFirst();
  }

  @Override
  public List<UUID> unresolved() {
    return jdbc.query(
        "SELECT id FROM payments WHERE status IN ('PENDING','UNKNOWN') AND updated_at < CURRENT_TIMESTAMP - INTERVAL '30 seconds' ORDER BY updated_at LIMIT 20",
        (r, i) -> r.getObject(1, UUID.class));
  }
}
