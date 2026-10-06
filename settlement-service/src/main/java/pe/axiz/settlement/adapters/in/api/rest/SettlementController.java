package pe.axiz.settlement.adapters.in.api.rest;

import java.util.*;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;

@RestController
public class SettlementController {
  private final JdbcTemplate jdbc;
  private final DynamoDbClient dynamo;
  private final String table;

  public SettlementController(JdbcTemplate jdbc, DynamoDbClient dynamo, Environment e) {
    this.jdbc = jdbc;
    this.dynamo = dynamo;
    table = e.getRequiredProperty("axiz.projection-table");
  }

  @GetMapping("/api/settlements/{id}")
  Object get(@PathVariable UUID id) {
    var result =
        dynamo
            .getItem(
                b ->
                    b.tableName(table)
                        .key(Map.of("pk", AttributeValue.builder().s(id.toString()).build()))
                        .consistentRead(true))
            .item();
    if (result.isEmpty())
      throw new NoSuchElementException("La proyección todavía no está disponible");
    var out = new TreeMap<String, String>();
    result.forEach((k, v) -> out.put(k, v.s() != null ? v.s() : v.n()));
    return out;
  }

  @GetMapping("/api/settlements/{id}/ledger")
  Object ledger(@PathVariable UUID id) {
    return jdbc.queryForList(
        "SELECT account,amount,currency FROM ledger WHERE payment_id=? ORDER BY account", id);
  }

  @GetMapping("/internal/settlement/backlog")
  Object backlog() {
    return Map.of(
        "pendingReceipts",
        jdbc.queryForObject(
            "SELECT COUNT(*) FROM receipt_outbox WHERE completed_at IS NULL", Long.class),
        "inbox",
        jdbc.queryForObject("SELECT COUNT(*) FROM inbox", Long.class));
  }
}
