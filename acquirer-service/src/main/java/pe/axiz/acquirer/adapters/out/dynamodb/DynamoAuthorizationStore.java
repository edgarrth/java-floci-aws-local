package pe.axiz.acquirer.adapters.out.dynamodb;

import java.util.*;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Repository;
import pe.axiz.acquirer.application.ports.out.AuthorizationStore;
import pe.axiz.acquirer.domain.Authorization;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;

@Repository
public class DynamoAuthorizationStore implements AuthorizationStore {
  private final DynamoDbClient db;
  private final String table;

  public DynamoAuthorizationStore(DynamoDbClient db, Environment env) {
    this.db = db;
    table = env.getRequiredProperty("axiz.acquirer-table");
  }

  private AttributeValue string(String v) {
    return AttributeValue.builder().s(v).build();
  }

  @Override
  public Decision reserve(Authorization a, boolean declined) {
    String fingerprint =
        a.merchantId()
            + "|"
            + a.amount().setScale(2).toPlainString()
            + "|"
            + a.currency()
            + "|"
            + a.token();
    String code = declined ? "DECLINED" : "AX" + a.paymentId().toString().substring(0, 8);
    Map<String, AttributeValue> item =
        Map.of(
            "pk",
            string(a.paymentId().toString()),
            "fingerprint",
            string(fingerprint),
            "status",
            string(declined ? "DECLINED" : "AUTHORIZED"),
            "code",
            string(code));
    try {
      db.putItem(
          b -> b.tableName(table).item(item).conditionExpression("attribute_not_exists(pk)"));
    } catch (ConditionalCheckFailedException ignored) {
      var existing = read(a.paymentId());
      if (!existing.get("fingerprint").s().equals(fingerprint))
        throw new IllegalStateException("El identificador ya pertenece a otra autorización");
      return decision(existing);
    }
    return decision(item);
  }

  private Map<String, AttributeValue> read(UUID id) {
    return db.getItem(
            b -> b.tableName(table).key(Map.of("pk", string(id.toString()))).consistentRead(true))
        .item();
  }

  private Decision decision(Map<String, AttributeValue> item) {
    return new Decision(item.get("status").s(), item.get("code").s());
  }

  @Override
  public Optional<Decision> find(UUID id) {
    var item = read(id);
    return item.isEmpty() ? Optional.empty() : Optional.of(decision(item));
  }
}
